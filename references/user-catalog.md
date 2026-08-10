# 用户硬件与报价 overlay

用户只和 Agent 对话。Agent 可从自然语言或图片提取事实，必要时查询品牌官网或向用户补问，再生成符合 `user-overlay.schema.json` 的 JSON。脚本不提问、不识图、不联网，也不扫描默认目录或环境变量。

先校验并规范化：

```bash
python -B scripts/import_user_catalog.py draft.json --validate-only --json
python -B scripts/import_user_catalog.py draft.json --output ./user-catalog.json --json
```

查询和检查必须显式传入同一 overlay；可重复传入，后传报价优先：

```bash
python -B scripts/query_components.py --category gpu --id BASE_ID --overlay ./user-catalog.json --currency USD --json
python -B scripts/check_compatibility.py --strict --require-complete --overlay ./user-catalog.json ...
```

- `quote_patches` 只更新价格、日期和备注，精确 `target_id` 继承基础库全部规格；不能用模糊型号继承。
- `components` 新 ID 必须以 `user-<category>-` 开头。`base_component_id` 只允许原始或官方英文字段可证明为完全相同 SKU 时继承；品牌或完整型号不同会以 `base_identity_conflict` 拒绝。已有基础条目的本地报价应使用 `quote_patches`，用户称呼用 `aliases`，不要克隆成另一型号。显式规格与基础事实冲突时同样拒绝。缺少兼容关键字段可以保存，但严格完整检查必须标为待复核。
- `aliases` 只做精确别名到稳定 ID 的映射；零匹配是未找到，多匹配是歧义错误。
- 用户价状态固定为 `user_quote`。基础 CNY 金额、状态和日期作为一个完整报价对象保留；查询其他币种时也只投影同一币种的金额、状态和日期。不同币种不换算、不混合预算或排序，查询时显式使用 `--currency`。
- `brand_en` / `model_en` 可由用户明确提供。内置别名仅覆盖已确认的官方英文品牌/系列；不机翻营销昵称。
- overlay 是用户本地文件，不进入 Skill、Git 仓库或发布包。
