# PC Build Assistant

`DIY装机助手` 是一个以中文硬件市场为数据基线的 Codex / OpenAI Skill，用于预算装机、整机推荐、旧机升级、配置补全、兼容检查和硬件选购问答；覆盖游戏与直播、生产力与本地 AI、外观海景房和紧凑或 ITX 主机，并可在模型需求与显卡显存、系统内存之间做双向容量评估。它也支持英文硬件科普、配置选择、升级和兼容性检查；英文输出继续使用中国市场人民币参考数据，不代表用户所在国家的实际价格、库存或可购买型号。

- Skill slug: `pc-build-assistant`
- 展示名称: `PC Build Assistant`
- 当前版本: `0.1.6`
- 许可证: MIT
- 价格参考日期: 以所选条目 `price_date` 为准；整包参考日期运行时读取数据文件 metadata
- 价格来源说明: 数据来自网络公开信息整理，仅供预算参考，不代表实时成交价或可下单价格。

## 能做什么

- 识别常见短需求，例如“预算 5000 主玩 3A 不要颜值”“预算 8000 白色海景房 主玩 3A”。
- 按 CPU、主板、内存、硬盘、显卡、电源、散热、机箱逐类查询候选。
- 生成整机总价、预算差额、取舍说明和下单前复核点。
- 使用 `scripts/check_compatibility.py --strict --require-complete` 检查接口、内存、显卡限长、电源余量、散热限高和机箱版型，并区分硬不兼容与字段待复核。
- Agent 可把用户文字或图片里的本地硬件与报价整理为外部 JSON overlay；基础库已有的规格按精确 ID 继承，缺失的兼容字段继续保守复核。
- 历史价格按精确组件 ID 查询已发布 GitHub tag，并绑定 tag 解引用后的 commit SHA；不在安装包中内置持续膨胀的历史数据库。
- 双向估算本地大模型与硬件容量：可由显卡显存/系统内存判断适合的模型档，也可由 30B、32B、70B 等参数量反推最低与推荐显存档，并从当前库返回显卡候选。

## 数据与价格说明

Skill 默认使用离线库中的网络公开价格参考。硬件价格和库存变化很快，结果不构成购买承诺；下单前应核对实时价格、库存、保修、具体型号后缀、颜色版本、尺寸和供电接口。

英文请求会按用户语言回答。存在显式用户 overlay 时按其中选定的单一币种查询和汇总，不与人民币行混算；否则默认仍列人民币。用户明确要求换算，或直接以美元等外币给出预算且没有匹配 overlay 时，才在线核对当日汇率并附加约合金额；人民币原价和汇率日期必须保留，换算值只是货币估算，不是当地报价。

每次输出配置报告时，Agent 都应提醒：

```markdown
价格参考日期: 以各行 price_date 为准；全单日期一致时可写统一日期，整包参考日期读取数据文件 metadata。
价格来自网络公开信息整理，仅供预算参考，实际购买前请复核实时价格、库存和具体型号后缀。
```

## 候选池与品牌中立说明

Skill 中的“热门采用 / 常见装机 / 新兴特色”候选池，只是基于网络公开价格、销量、装机采用率、渠道覆盖、规格透明度和数据完整度等公开信号做排序辅助。它不代表项目作者对任何品牌的商业倾向、背书或贬损，也不构成购买引导。

本 Skill 的输出只用于 DIY 知识科普和配置选择参考。最终购买仍应结合实时价格、库存、保修、售后、具体型号后缀、颜色版本、尺寸和个人偏好自行复核。所有品牌名称和商标归各自权利人所有。

## 使用方式

安装到支持 Skill 的环境后，可以直接描述预算和用途：

```markdown
预算 5000，主玩 3A，不要颜值
预算 8000，白色海景房，主玩 3A
预算 12000，黑色无光，主玩 3A
```

如果手动运行脚本，可在 Skill 根目录执行：

```bash
python -B scripts/query_components.py --category gpu --budget 5000 --sort tier --limit 20
python -B scripts/check_compatibility.py --strict --require-complete --cpu <cpu-id> --mb <mb-id> --mem <mem-id> --storage <ssd-id> --gpu <gpu-id> --psu <psu-id> --cooler <cooler-id> --case <case-id>
python -B scripts/import_user_catalog.py <draft.json> --validate-only --json
python -B scripts/query_components.py --category gpu --overlay <user-catalog.json> --currency USD --json
python -B scripts/query_price_history.py --id <component-id> --versions 5 --json
python -B scripts/validate_library.py
```

## 文件结构

```text
.
├── SKILL.md
├── LICENSE
├── agents/openai.yaml
├── data/
│   ├── components.yaml
│   ├── cases.yaml
│   ├── displays.yaml
│   ├── game_fps.yaml
│   ├── hardware_name_aliases.json
│   └── price_floors.yaml
├── references/
│   ├── routing.md
│   ├── selection-policy.md
│   ├── workflows.md
│   ├── scenarios.md
│   ├── hardware-faq.md
│   ├── english-usage.md
│   ├── game-performance.md
│   ├── price-history.md
│   ├── user-catalog.md
│   ├── user-overlay.schema.json
│   ├── pricing.md
│   ├── compatibility.md
│   └── hardware-scope.md
└── scripts/
    ├── component_inference.py
    ├── catalog_overlay.py
    ├── import_user_catalog.py
    ├── query_components.py
    ├── query_game_fps.py
    ├── query_price_history.py
    ├── check_compatibility.py
    └── validate_library.py
```

## 发布边界

本仓库只包含通用 Codex / OpenAI Skill 发布所需文件。`agents/openai.yaml` 是 Codex / OpenAI Skill 的展示元数据，不作为 ClawHub 元数据使用。非运行资料、内部记录和测试过程文件不包含在本发布包中。ClawHub 发布使用单独的 OpenClaw 风格发布目录，二者的元数据和说明面保持分离。
