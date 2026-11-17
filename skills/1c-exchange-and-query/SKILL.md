---
name: 1c-exchange-and-query
description: Autonomous workflow for 1C Data Conversion 2.x (КД 2.x) exchange rules and live 1C infobase querying via `1c-harness` (`decompile`, `rules unpack/pack`, `query`). Use when modifying 1C exchange rules or inspecting live server/file 1C infobases over SSH/COM.
---

# 1C Exchange Rules (КД 2.x) & Live Queries

## 1. Inspecting Source & Target Configurations
Decompile `.cf` files into read-only XML/BSL trees to verify exact attribute, enum, catalog, and register names:
```powershell
harness decompile <path_to.cf> <out_dir> [--ib <ib_dir>]
```

## 2. Modifying КД 2.x Rules (`gitrules`)
Never edit monolithic `ExchangeRules.xml` by hand.
1. **Unpack:**
   ```powershell
   harness rules unpack <rules.zip_or_xml> <unpacked_dir>
   ```
2. **Verify direction:**
   - Check `<Источник>` and `<Приемник>` in `_raw/ExchangeRules.xml` and `_raw/CorrespondentExchangeRules.xml`.
   - `ПослеЗагрузки` / `ПриЗагрузке` handlers for `A -> B` execute on the **target** base `B`.
   - In a rules archive exported from base `B`, `A -> B` is typically `CorrespondentExchangeRules.xml`, whereas in an archive from base `A`, `A -> B` is `ExchangeRules.xml`.
3. **Edit & Syntax Check:**
   - Edit extracted `.bsl` files (e.g. `ПравилаКонвертацииОбъектов/Документы/.../Ext/ПослеЗагрузки.bsl`).
   - Run `bsl-language-server analyze -s <ext_dir> -o <report_dir> -r console --silent` to verify 0 parse/syntax errors.
4. **Pack & Validate XML:**
   ```powershell
   harness rules pack <unpacked_dir> <out.zip>
   ```

## 3. Querying Live 1C Infobases (`harness query`, Windows COM)
`harness query` executes 1C query language via `V83.COMConnector` (locally or over `ssh`) and emits JSON `{columns, rows, seconds}`:
```powershell
harness query --server "<1c-server>" --ref "<infobase_name>" -q "ВЫБРАТЬ ПЕРВЫЕ 5 Ссылка ИЗ Документ.ОтчетОРозничныхПродажах"
```
- **Credentials & SSH:** `ONEC_SSH`, `ONEC_USR`, and `ONEC_PWD` are automatically resolved from process env or Windows `HKCU\Environment`. Do not ask the user for passwords when these are set.
- **Force local execution:** pass `--ssh local` to ignore `ONEC_SSH`.
- **1C COM requirements:** `comcntr.dll` (matching PowerShell bitness, usually 64-bit) must be registered (`regsvr32 <platform_bin>\comcntr.dll` from an elevated prompt) on the host where `V83.COMConnector` runs.
