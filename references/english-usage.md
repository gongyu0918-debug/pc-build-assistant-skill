# English usage

Read this file when the user's request is primarily in English or explicitly asks for an English answer.

## Activation

- Activate this layer when the user's natural-language request is primarily English or the user explicitly asks for an English answer.
- Do not activate it merely because a Chinese request contains English hardware models, software names, acronyms, or commands.
- For a mixed-language request, follow an explicit output-language instruction. If none is given, use the predominant language of the natural-language clauses and ignore model names when deciding.
- In follow-up turns, honor the user's latest explicit language choice. Stop loading this layer after the user clearly switches back to Chinese.
- Load this file before any Chinese reference required for the task so the translation and output boundary is active first.

## Translation layer

- Treat Chinese references as the single source of selection rules. Read only the references required by the request and use their content as internal evidence; do not copy their headings, sentences, or user-facing labels.
- After querying and checking compatibility, reduce the result to structured facts: exact model, specification, CNY price, price date, compatibility finding, trade-off, and verification item. Write the final response from those facts in English.
- Use these user-facing labels: `CPU`, `Motherboard`, `Memory`, `Storage`, `Graphics card`, `CPU cooler`, `Power supply`, `Case`, `Case fans`, `Display`, `Unit price`, `Total`, `Budget difference`, `Compatibility`, and `Local buying note`.
- Before sending, review the answer for Chinese text. Chinese characters are allowed only inside an exact product or model name returned by the catalog. Rewrite Chinese headings, explanations, compatibility messages, dates, and internal status text in English.

## Reference routing

- For a complete build, read `routing.md`, `selection-policy.md`, the relevant part of `scenarios.md`, `pricing.md`, and `compatibility.md` after this file. After drafting the first parts list, apply the budget-closure, default-capacity, component-quality, and balance rules from those references before writing the final answer.
- In a complete build, turn every scenario-required item with an available catalog candidate into a priced row. Do not leave a queryable required item as an unpriced budget reserve.
- For an upgrade, configuration completion, or compatibility review, read `workflows.md` plus only the scenario, pricing, selection, and compatibility references needed for that request.
- For a hardware-selection question, read the relevant section of `hardware-faq.md`. Query exact products only when the question requires models or prices.
- This routing is procedural. Do not restate or maintain a second copy of the Chinese selection rules here.

## Scope

- Reply in English and keep hardware model names unchanged.
- Support desktop PC build planning, upgrades, configuration completion, compatibility reviews, budget allocation, and hardware selection explanations.
- Keep the existing scenario, component-selection, price, and compatibility rules. Run the same query and strict compatibility scripts before recommending a complete build.
- Do not expand into software tutorials, operating-system troubleshooting, or second-hand hardware.
- Do not use this skill for laptops, enterprise or server procurement, ordering or payment, remote control, or security-isolation work.

## Market boundary

- The bundled catalog and channel quotes describe the Chinese market and use CNY.
- Do not present a currency conversion as a local retail quote. Do not claim that a listed model, suffix, color, warranty, stock level, or price is available in the user's country.
- If the user asks what to buy locally, provide hardware criteria and a China-market reference build, then ask them to verify equivalent local SKUs, retailers, warranty terms, stock, and prices.
- Do not convert currencies by default. A budget stated in USD or another non-CNY currency counts as a request for conversion: verify a current exchange rate before querying, translate the budget into a CNY target, retain the CNY total, and show an approximate total in the user's currency. State the exchange-rate date and make clear that the conversion is not a local retail quote.
- Keep the CNY component total separate from local tax, shipping, rebates, and checkout-only discounts. Show those separately only when the user asks.
- Exclude RTX 5090D and RTX 5090D V2 from default English recommendations. Compare them only when the user explicitly asks about China-region GPU variants.
- Cases, CPU coolers, case fans, and power supplies vary strongly by region. Give each selected row a short, concrete local buying note such as `Check for a local 650W ATX 3.1 equivalent`.
- Put that note directly in each selected case, cooler, fan, and power-supply row. Do not replace the row notes with one generic verification list.
- Leave the local-verification field blank for CPU, motherboard, memory, storage, and graphics-card rows unless that exact SKU has a documented regional restriction. Do not add generic stock, warranty, or availability notes to those rows.

## User-facing output

- For a complete build, use the columns `Component | Exact model | Unit price | Local buying note`. Keep exact product model names.
- Leave `Local buying note` blank for CPU, motherboard, memory, storage, and graphics-card rows. Fill it only for each selected CPU cooler, power supply, case, and case-fan row, unless a standard row has a documented regional restriction.
- Give one primary build. Add one compact alternative only when the request has a genuine decision fork.
- Show every selected component price in CNY, the total in CNY, the reference date, compatibility result, trade-offs, and specific items that still need verification.
- Take the reference date only from the current query result or bundled catalog metadata used for the quote. Never reuse a date from an example, an older report, or memory.
- Keep the report compact: eight core component rows, optional fan or display rows when requested, total, compatibility result, two to four useful trade-offs, and one market note. Do not expose command names, exit codes, internal status labels, test markers, raw templates, or Chinese column labels.
- Never expose words such as `strict`, `complete`, pass/check counts, skipped checks, or script status. When appropriate, say `The listed parts are compatible based on the available specifications`, then name only concrete items that still require verification.
- A separate verification list is only for concrete compatibility uncertainties such as QVL, physical clearance, connector availability, or an unknown catalog field. Do not use it for generic SKU, stock, warranty, or availability reminders.
- Use one natural market note that fits the request instead of repeating a fixed disclaimer:
  - Full quote: `CNY prices are China-market references dated YYYY-MM-DD; local price and stock can differ.`
  - Local buying request: `Treat these as China-market equivalents and verify local SKUs, warranty, stock, and store pricing.`
  - Currency request: `Using the exchange rate checked on YYYY-MM-DD, the CNY total is about USD X. This is a currency conversion; local prices and stock can differ.`
- Do not stack these notes or repeat them after every component.

## Final check

- Every queryable item required by the loaded Chinese scenario is a priced row and is included in the total.
- Exact catalog product names remain unchanged; Chinese text appears nowhere else.
- Each selected cooler, power supply, case, and case-fan row has its own short local buying note.
- Every displayed price date comes from the selected query result or current catalog metadata.
