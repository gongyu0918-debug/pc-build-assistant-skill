#!/usr/bin/env python3
"""Resolve explicit user catalog overlays without mutating the bundled catalog."""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml


SUPPORTED_CURRENCIES = {"CNY", "USD", "EUR", "GBP", "JPY", "TWD"}


@dataclass(frozen=True)
class CategoryContract:
    section: str
    critical: tuple[str, ...]
    specs: frozenset[str]


@dataclass(frozen=True)
class FieldContract:
    kind: str
    minimum: float | None = None
    choices: tuple[str, ...] = ()


def _fields(*names):
    return frozenset(names)


# One tested source of truth retains category-specific facts instead of flattening
# CPUs, boards, GPUs and enclosures into one generic component shape.
CATEGORY_CONTRACTS = {
    "cpu": CategoryContract("cpus", ("socket", "power_w"), _fields(
        "platform", "socket", "power_w", "integrated_graphics", "cores_threads", "cores", "threads")),
    "mb": CategoryContract("motherboards", ("socket", "memory_generations", "form_factor"), _fields(
        "platform", "socket", "chipset", "memory_generations", "memory_slots", "memory_freq_max",
        "m2_slots", "sata_ports", "display_outputs", "form_factor", "color")),
    "memory": CategoryContract("memory", ("generation", "capacity_gb", "module_count"), _fields(
        "generation", "capacity_gb", "module_count", "frequency_mt", "timing", "color", "rgb")),
    "storage": CategoryContract("storage", ("interface", "capacity_gb"), _fields(
        "capacity_gb", "capacity_tb", "form_factor", "interface", "pcie_generation", "storage_type",
        "dram_cache", "dram_cache_mb", "series")),
    "gpu": CategoryContract("gpus", ("chip", "length_mm", "power_w"), _fields(
        "chip", "gpu_vendor", "vram_gb", "memory_type", "memory_bus_bit", "memory_bandwidth_gbps",
        "gpu_cooling", "gpu_radiator_required", "length_mm", "power_w", "power_connectors",
        "requires_16pin_psu", "color", "rgb")),
    "cooler": CategoryContract("coolers", ("type", "socket_support"), _fields(
        "type", "height_mm", "radiator_mm", "socket_support", "color", "rgb")),
    "psu": CategoryContract("psus", ("wattage_w", "form_factor"), _fields(
        "wattage_w", "form_factor", "length_mm", "efficiency", "modular", "native_16pin_gpu_power", "color")),
    "case": CategoryContract("cases", ("motherboard_support", "gpu_length_mm", "cpu_cooler_height_mm", "psu_support"), _fields(
        "colors", "color", "motherboard_support", "gpu_length_mm", "cpu_cooler_height_mm",
        "radiator_support", "fan_mounts", "fan_slots_count", "psu_support", "psu_length_mm",
        "psu_length_recommended_mm", "psu_length_condition", "air_flow_type", "has_dust_filter", "is_showcase")),
    "display": CategoryContract("displays", ("resolution", "refresh_rate_hz"), _fields(
        "resolution", "size_inch", "refresh_rate_hz", "color")),
    "fan": CategoryContract("fans", ("size_mm",), _fields(
        "size_mm", "pack_count", "blade_direction", "color", "rgb", "is_linkable", "has_screen",
        "radiator_fan_bundle_mm", "fan_type", "default_recommend")),
}
CATEGORY_SECTIONS = {name: contract.section for name, contract in CATEGORY_CONTRACTS.items()}
ALLOWED_SPEC_FIELDS = frozenset().union(*(contract.specs for contract in CATEGORY_CONTRACTS.values()))
FIELD_CONTRACTS = {
    **{name: FieldContract("string") for name in (
        "platform", "socket", "chipset", "form_factor", "color", "generation", "timing", "interface",
        "storage_type", "series", "chip", "gpu_vendor", "memory_type", "gpu_cooling", "type", "efficiency",
        "blade_direction", "fan_type", "resolution", "psu_length_condition", "cores_threads", "fan_mounts",
        "air_flow_type")},
    **{name: FieldContract("number", 0.000001) for name in (
        "power_w", "cores", "threads", "memory_slots", "memory_freq_max", "m2_slots", "sata_ports",
        "capacity_gb", "capacity_tb", "module_count", "frequency_mt", "pcie_generation", "dram_cache_mb",
        "vram_gb", "memory_bus_bit", "memory_bandwidth_gbps", "length_mm", "height_mm", "radiator_mm",
        "wattage_w", "gpu_length_mm", "cpu_cooler_height_mm", "fan_slots_count", "psu_length_mm",
        "psu_length_recommended_mm", "size_inch", "refresh_rate_hz", "size_mm", "pack_count",
        "radiator_fan_bundle_mm")},
    **{name: FieldContract("boolean") for name in (
        "integrated_graphics", "rgb", "dram_cache", "gpu_radiator_required", "requires_16pin_psu", "modular",
        "native_16pin_gpu_power", "has_dust_filter", "is_showcase", "is_linkable", "has_screen", "default_recommend")},
    **{name: FieldContract("string_list") for name in (
        "memory_generations", "display_outputs", "colors", "power_connectors", "socket_support",
        "motherboard_support", "psu_support")},
    "radiator_support": FieldContract("number_list"),
}


class OverlayError(ValueError):
    """Machine-readable overlay failure."""

    def __init__(self, code: str, message: str, path: str = "$"):
        super().__init__(message)
        self.code, self.message, self.path = code, message, path

    def as_dict(self):
        return {"code": self.code, "path": self.path, "message": self.message}


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OverlayError("duplicate_key", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise OverlayError("invalid_number", f"non-finite number is forbidden: {value}")


def load_json_strict(source, *, max_bytes=5_000_000):
    try:
        if hasattr(source, "read"):
            payload = source.read(max_bytes + 1)
        else:
            with Path(source).open("rb") as handle:
                payload = handle.read(max_bytes + 1)
    except (OSError, UnicodeError) as exc:
        raise OverlayError("file_read_error", "unable to read overlay JSON") from exc
    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OverlayError("invalid_encoding", "overlay JSON must be UTF-8") from exc
    elif isinstance(payload, str):
        text = payload
    else:
        raise OverlayError("file_read_error", "overlay source must provide text or bytes")
    if len(text.encode("utf-8")) > max_bytes:
        raise OverlayError("file_too_large", f"overlay exceeds {max_bytes} bytes")
    try:
        return json.loads(text, object_pairs_hook=_pairs_no_duplicates, parse_constant=_reject_constant)
    except OverlayError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise OverlayError("invalid_json", str(exc)) from exc


def _keys(obj, allowed, path):
    if not isinstance(obj, dict):
        raise OverlayError("wrong_type", "expected object", path)
    extra = sorted(set(obj) - set(allowed))
    if extra:
        raise OverlayError("unknown_field", f"unknown field(s): {', '.join(extra)}", path)


def _iso_date(value, path):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise OverlayError("invalid_date", "expected ISO date YYYY-MM-DD", path)
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise OverlayError("invalid_date", "expected ISO date YYYY-MM-DD", path) from exc


def _price(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise OverlayError("invalid_price", "price must be a positive finite number", path)


def _validate_spec_value(field, value, path):
    contract = FIELD_CONTRACTS.get(field)
    if contract is None:
        return
    if contract.kind == "string":
        valid = isinstance(value, str) and bool(value.strip())
    elif contract.kind == "number":
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= contract.minimum
    elif contract.kind == "boolean":
        valid = isinstance(value, bool)
    elif contract.kind == "string_list":
        valid = isinstance(value, list) and bool(value) and all(isinstance(x, str) and x.strip() for x in value)
    elif contract.kind == "number_list":
        valid = isinstance(value, list) and bool(value) and all(not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in value)
    else:
        valid = False
    if not valid:
        raise OverlayError("invalid_spec", f"invalid {field} value", path)


def validate_overlay(doc):
    _keys(doc, ("schema_version", "currency", "quote_patches", "components", "aliases"), "$")
    if doc.get("schema_version") != "1.0":
        raise OverlayError("unsupported_schema", "schema_version must be 1.0", "$.schema_version")
    currency = doc.get("currency")
    if currency not in SUPPORTED_CURRENCIES:
        raise OverlayError("invalid_currency", f"currency must be one of {sorted(SUPPORTED_CURRENCIES)}", "$.currency")
    for field in ("quote_patches", "components", "aliases"):
        if field in doc and not isinstance(doc[field], list):
            raise OverlayError("wrong_type", "expected array", f"$.{field}")
        if len(doc.get(field, [])) > 5000:
            raise OverlayError("too_many_items", "array limit is 5000", f"$.{field}")

    for i, patch in enumerate(doc.get("quote_patches", [])):
        p = f"$.quote_patches[{i}]"
        _keys(patch, ("target_id", "price", "price_date", "note"), p)
        if not isinstance(patch.get("target_id"), str) or not patch["target_id"].strip():
            raise OverlayError("invalid_id", "target_id is required", p + ".target_id")
        _price(patch.get("price"), p + ".price")
        _iso_date(patch.get("price_date"), p + ".price_date")
        if "note" in patch and (not isinstance(patch["note"], str) or len(patch["note"]) > 1000):
            raise OverlayError("wrong_type", "note must be a string of at most 1000 characters", p + ".note")

    ids = set()
    for i, component in enumerate(doc.get("components", [])):
        p = f"$.components[{i}]"
        _keys(component, ("id", "category", "brand", "model", "brand_en", "model_en", "base_component_id", "specs", "price", "price_date", "note"), p)
        category, item_id = component.get("category"), component.get("id")
        if category not in CATEGORY_CONTRACTS:
            raise OverlayError("invalid_category", f"unknown category: {category}", p + ".category")
        if not isinstance(item_id, str) or not re.fullmatch(rf"user-{re.escape(category)}-[a-z0-9][a-z0-9._-]*", item_id):
            raise OverlayError("invalid_id", f"id must start with user-{category}-", p + ".id")
        if item_id in ids:
            raise OverlayError("duplicate_id", f"duplicate component id: {item_id}", p + ".id")
        ids.add(item_id)
        for field in ("brand", "model"):
            if not isinstance(component.get(field), str) or not component[field].strip():
                raise OverlayError("missing_identity", f"{field} is required", p + f".{field}")
        for field in ("brand_en", "model_en"):
            if field in component and (not isinstance(component[field], str) or not component[field].strip()):
                raise OverlayError("wrong_type", f"{field} must be a non-empty string", p + f".{field}")
        if "note" in component and (not isinstance(component["note"], str) or len(component["note"]) > 1000):
            raise OverlayError("wrong_type", "note must be a string of at most 1000 characters", p + ".note")
        if "base_component_id" in component and (
            not isinstance(component["base_component_id"], str) or not component["base_component_id"].strip()
        ):
            raise OverlayError("invalid_id", "base_component_id must be a non-empty exact ID", p + ".base_component_id")
        specs = component.get("specs", {})
        _keys(specs, CATEGORY_CONTRACTS[category].specs, p + ".specs")
        for field, value in specs.items():
            _validate_spec_value(field, value, p + f".specs.{field}")
        if "price" in component:
            _price(component["price"], p + ".price")
            _iso_date(component.get("price_date"), p + ".price_date")
        elif "price_date" in component:
            raise OverlayError("orphan_price_date", "price_date requires price", p + ".price_date")

    for i, alias in enumerate(doc.get("aliases", [])):
        p = f"$.aliases[{i}]"
        _keys(alias, ("alias", "target_id"), p)
        if not all(isinstance(alias.get(k), str) and alias[k].strip() for k in ("alias", "target_id")):
            raise OverlayError("invalid_alias", "alias and target_id are required", p)
    return doc


def normalize_overlay(doc):
    validate_overlay(doc)
    result = copy.deepcopy(doc)
    result.setdefault("quote_patches", [])
    result.setdefault("components", [])
    result.setdefault("aliases", [])
    return result


def _compact(value):
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _identity_keys(item, alias_data, category, *, include_explicit):
    """Return raw-derived SKU keys, optionally including stored official identity."""
    raw_identity = {"brand": item.get("brand"), "model": item.get("model")}
    normalized = normalize_item_names(raw_identity, alias_data, category)
    keys = set()
    for brand_field, model_field in (("brand", "model"), ("brand_en", "model_en")):
        brand = _compact(normalized.get(brand_field))
        model = _compact(normalized.get(model_field))
        if brand and model:
            keys.add((brand, model))
    if include_explicit:
        brand = _compact(item.get("brand_en"))
        model = _compact(item.get("model_en"))
        if brand and model:
            keys.add((brand, model))
    return keys


def _same_component_identity(component, base, alias_data, category):
    component_keys = _identity_keys(component, alias_data, category, include_explicit=False)
    base_keys = _identity_keys(base, alias_data, category, include_explicit=True)
    if not (component_keys & base_keys):
        return False
    normalized_base = normalize_item_names(base, alias_data, category)
    for field, raw_field in (("brand_en", "brand"), ("model_en", "model")):
        if field not in component:
            continue
        allowed = {_compact(normalized_base.get(field)), _compact(normalized_base.get(raw_field))} - {""}
        if _compact(component[field]) not in allowed:
            return False
    return True


CATALOGUE_FILES = (
    (("components.yaml", "components.txt"), ("cpus", "motherboards", "memory", "storage", "gpus", "coolers", "psus", "fans")),
    (("cases.yaml", "cases.txt"), ("cases",)),
    (("displays.yaml", "displays.txt"), ("displays",)),
)


def load_catalog_sections(data_dir):
    """Load the bundled YAML catalogue, including Red package .txt adapters."""
    data_dir = Path(data_dir)
    sections = {}
    for filenames, section_names in CATALOGUE_FILES:
        path = next((data_dir / name for name in filenames if (data_dir / name).is_file()), None)
        if path is None:
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                document = yaml.safe_load(handle) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise OverlayError("catalog_read_error", f"unable to read bundled catalog: {path.name}") from exc
        if not isinstance(document, dict):
            raise OverlayError("catalog_read_error", f"bundled catalog root must be an object: {path.name}")
        for section in section_names:
            items = document.get(section, [])
            if not isinstance(items, list):
                raise OverlayError("catalog_read_error", f"catalog section must be an array: {section}")
            sections[section] = items
    return sections


def load_name_aliases(data_dir):
    path = Path(data_dir) / "hardware_name_aliases.json"
    if not path.exists():
        return {"schema_version": "1.0", "brands": [], "series": []}
    doc = load_json_strict(path)
    _keys(doc, ("schema_version", "brands", "series"), "$")
    if doc.get("schema_version") != "1.0":
        raise OverlayError("unsupported_alias_schema", "alias schema_version must be 1.0")
    if not isinstance(doc.get("brands"), list) or not isinstance(doc.get("series"), list):
        raise OverlayError("invalid_alias_catalog", "brands and series must be arrays")
    officials, seen_aliases, series_keys = set(), {}, set()
    for index, entry in enumerate(doc["brands"]):
        p = f"$.brands[{index}]"
        _keys(entry, ("official", "aliases"), p)
        official, aliases = entry.get("official"), entry.get("aliases")
        if not isinstance(official, str) or not official.strip() or official in officials:
            raise OverlayError("duplicate_official_name", f"invalid or duplicate official name: {official}", p)
        if not isinstance(aliases, list) or not aliases or not all(isinstance(x, str) and x.strip() for x in aliases):
            raise OverlayError("invalid_alias_catalog", "aliases must be a non-empty string array", p)
        officials.add(official)
        for alias in aliases:
            key = _compact(alias)
            if key in seen_aliases and seen_aliases[key] != official:
                raise OverlayError("overlapping_alias", f"alias {alias} maps to multiple official names", p)
            seen_aliases[key] = official
    for index, rule in enumerate(doc["series"]):
        p = f"$.series[{index}]"
        _keys(rule, ("brand", "official", "categories", "patterns"), p)
        key = (rule.get("brand"), rule.get("official"))
        patterns, categories = rule.get("patterns"), rule.get("categories")
        if key in series_keys or rule.get("brand") not in officials:
            raise OverlayError("invalid_series_alias", f"invalid or duplicate series rule: {key}", p)
        if not isinstance(patterns, list) or not patterns or not isinstance(categories, list) or not categories:
            raise OverlayError("invalid_series_alias", "patterns and categories must be non-empty arrays", p)
        if any(category not in CATEGORY_CONTRACTS for category in categories):
            raise OverlayError("invalid_series_alias", "series rule contains an unknown category", p)
        try:
            for pattern in patterns:
                re.compile(pattern)
        except (TypeError, re.error) as exc:
            raise OverlayError("invalid_series_alias", f"invalid regex pattern: {exc}", p) from exc
        series_keys.add(key)
    return doc


def normalize_item_names(item, alias_data, category=None):
    result = dict(item)
    if result.get("brand_en") and result.get("model_en"):
        result["normalization_status"] = "explicit"
        return result
    brand_text = _compact(result.get("brand"))
    brand_en = result.get("brand_en")
    for entry in alias_data.get("brands", []):
        if brand_text in {_compact(x) for x in entry.get("aliases", [])}:
            brand_en = entry["official"]
            break
    model = str(result.get("model") or "")
    model_en = result.get("model_en")
    matched = None
    for rule in alias_data.get("series", []):
        if rule.get("brand") != brand_en:
            continue
        if category not in rule.get("categories", []):
            continue
        if any(re.search(pattern, model, flags=re.IGNORECASE) for pattern in rule.get("patterns", [])):
            matched = rule["official"]
            break
    if brand_en:
        result["brand_en"] = brand_en
    if matched:
        model_en = model
        for entry in alias_data.get("brands", []):
            if entry.get("official") == brand_en:
                for alias in sorted(entry.get("aliases", []), key=len, reverse=True):
                    model_en = re.sub(re.escape(alias), brand_en, model_en, flags=re.IGNORECASE)
        rule = next(rule for rule in alias_data["series"] if rule["brand"] == brand_en and rule["official"] == matched and category in rule["categories"])
        for pattern in rule["patterns"]:
            match = re.search(pattern, model_en, flags=re.IGNORECASE)
            if not match:
                continue
            replacement = matched
            if _compact(matched) in _compact(model_en) and _compact(match.group(0)) != _compact(matched):
                replacement = ""
            model_en = re.sub(pattern, replacement, model_en, flags=re.IGNORECASE)
        model_en = re.sub(r"[^\x20-\x7E]+", " ", model_en)
        model_en = re.sub(r"\s+", " ", model_en).strip()
        if model_en and _compact(model_en) != _compact(brand_en):
            result["model_en"] = model_en
            result["normalization_status"] = "official_alias_partial"
        else:
            result["normalization_status"] = "brand_only"
    elif model_en:
        result["model_en"] = model_en
        result["normalization_status"] = "explicit"
    elif brand_en:
        result["normalization_status"] = "brand_only"
    else:
        result["normalization_status"] = "unverified"
    return result


def _apply_user_quote(item, quote, currency):
    """Attach one active user quote while retaining the base CNY quote object."""
    if item.get("price_cny") is not None:
        item.setdefault("base_price_cny", item.get("price_cny"))
        item.setdefault("base_price_status", item.get("price_status"))
        item.setdefault("base_price_date", item.get("price_date"))
    item.update({
        "user_price": quote["price"],
        "user_price_currency": currency,
        "user_price_date": quote["price_date"],
        "user_quote_note": quote.get("note"),
        # Compatibility aliases for callers written before the quote object split.
        "active_price": quote["price"],
        "price_currency": currency,
        "price_status": "user_quote",
        "price_date": quote["price_date"],
    })


def resolve_catalog_documents(sections, documents, *, data_dir, normalize_names=True):
    """Resolve already-parsed overlays; shared by import, query, and check."""
    alias_data = load_name_aliases(data_dir)
    section_categories = {contract.section: category for category, contract in CATEGORY_CONTRACTS.items()}
    resolved = {
        name: [
            normalize_item_names(item, alias_data, section_categories.get(name))
            if normalize_names else dict(item)
            for item in items
        ]
        for name, items in sections.items()
    }
    by_id = {}
    category_by_id = {}
    for category, section in CATEGORY_SECTIONS.items():
        for item in resolved.get(section, []):
            if item["id"] in by_id:
                raise OverlayError("duplicate_base_id", f"duplicate base id: {item['id']}")
            by_id[item["id"]] = item
            category_by_id[item["id"]] = category
            if item.get("price_cny"):
                item.setdefault("base_price_cny", item["price_cny"])
                item.setdefault("active_price", item["price_cny"])
                item.setdefault("price_currency", "CNY")

    alias_index = {}
    for document in documents or []:
        doc = normalize_overlay(document)
        currency = doc["currency"]
        new_ids = {x["id"] for x in doc["components"]}
        collisions = sorted(new_ids & set(by_id))
        if collisions:
            raise OverlayError("duplicate_id", f"component id already exists: {collisions[0]}")
        for component in doc["components"]:
            base = by_id.get(component.get("base_component_id")) if component.get("base_component_id") else None
            if component.get("base_component_id") and base is None:
                raise OverlayError("unknown_target", f"base component does not exist: {component['base_component_id']}")
            if base and category_by_id[component["base_component_id"]] != component["category"]:
                raise OverlayError("category_conflict", "base_component_id category does not match new component")
            if base and not _same_component_identity(component, base, alias_data, component["category"]):
                raise OverlayError(
                    "base_identity_conflict",
                    "base_component_id may inherit specifications only for the exact same bilingual SKU; use quote_patches and aliases for an existing catalog item",
                )
            item = dict(base or {})
            if base:
                for field in (
                    "price_cny", "base_price_cny", "base_price_status", "base_price_date",
                    "user_price", "user_price_currency", "user_price_date", "active_price",
                    "price_currency", "price_status", "price_date", "user_quote_note",
                ):
                    item.pop(field, None)
            supplied_specs = component.get("specs", {})
            for field, value in supplied_specs.items():
                if base and base.get(field) not in (None, "", []) and base.get(field) != value:
                    raise OverlayError("spec_conflict", f"{field} conflicts with inherited base specification")
                item[field] = value
            item.update({"id": component["id"], "brand": component["brand"], "model": component["model"]})
            if base:
                item["base_component_id"] = component["base_component_id"]
            for key in ("brand_en", "model_en", "note"):
                if component.get(key) is not None:
                    item[key] = component[key]
            if component.get("price") is not None:
                _apply_user_quote(item, component, currency)
            missing = [field for field in CATEGORY_CONTRACTS[component["category"]].critical if item.get(field) in (None, "", [])]
            if missing:
                item["_overlay_incomplete_fields"] = missing
            item = normalize_item_names(item, alias_data, component["category"])
            resolved.setdefault(CATEGORY_SECTIONS[component["category"]], []).append(item)
            by_id[item["id"]] = item
            category_by_id[item["id"]] = component["category"]
        for patch in doc["quote_patches"]:
            target = by_id.get(patch["target_id"])
            if target is None:
                raise OverlayError("unknown_target", f"quote target does not exist: {patch['target_id']}")
            _apply_user_quote(target, patch, currency)
        for alias in doc["aliases"]:
            if alias["target_id"] not in by_id:
                raise OverlayError("unknown_target", f"alias target does not exist: {alias['target_id']}")
            key = _compact(alias["alias"])
            targets = alias_index.setdefault(key, set())
            if targets and alias["target_id"] not in targets:
                raise OverlayError("ambiguous_alias", f"alias resolves to multiple IDs: {', '.join(sorted(targets | {alias['target_id']}))}")
            targets.add(alias["target_id"])
    return resolved, by_id, alias_index


def resolve_catalog(sections, overlay_paths, *, data_dir):
    """Return copied sections plus alias index. Later quote patches win."""
    documents = [load_json_strict(path) for path in (overlay_paths or [])]
    return resolve_catalog_documents(sections, documents, data_dir=data_dir)


def resolve_id(token, by_id, alias_index):
    if token in by_id:
        return token
    targets = sorted(alias_index.get(_compact(token), set()))
    if not targets:
        return None
    if len(targets) > 1:
        raise OverlayError("ambiguous_alias", f"alias resolves to multiple IDs: {', '.join(targets)}")
    return targets[0]
