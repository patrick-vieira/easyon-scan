# Protocolo de fotos reais / Real-photo protocol

## PT-BR

**A precisão em fotos reais ainda não foi validada.** Este protocolo recolhe
comparações observadas; resíduos dos marcadores não estimam precisão física.

1. Identifique peça e captura. Meça o papel plano com instrumento independente
   pelo menos duas vezes; registre instrumento, resolução e dispersão. Em modo
   livre, largura/altura são lados menor/maior do retângulo envolvente mínimo;
   em modo fixo seguem os eixos. Área exige referência independente, descontando furos.
2. Imprima cartões em tamanho real e confira cada quadrado preto de 90 mm.
   Inclua uma régua de controle com intervalo conhecido de 100 mm no mesmo
   plano e visível na foto. Registre comprimento nominal e medido. Use régua
   escura sobre mesa escura, fora dos moldes e sem tocar cartões: objetos claros
   podem virar peças falsas. A régua é para inspeção manual; não é detectada
   nem usada automaticamente para calibrar.
3. Papel e quatro ou mais cartões distintos devem estar planos numa mesa escura
   e fosca. Cerque todo o molde com cartões e folgas visíveis; separe peças
   por pelo menos 3 mm. Evite sombras, reflexos e lente ultrawide.
4. Fotografe de cima com cartões e peças completos. Preserve original, EXIF e
   resolução. Registre aparelho, lente, zoom, iluminação, inclinação aproximada
   e perfil de calibração. Repita com reposicionamento, distâncias e inclinações
   diferentes; preserve também capturas rejeitadas.
5. Salve JSON, DXF e preview por captura e confira contornos, furos e símbolos.
   Registre motivos de exclusão; relate quantidade de amostras e resultados
   separados por aparelho e condição. Não descarte erros silenciosamente.

Ficha auxiliar: uma linha por captura, com estas colunas:

```csv
capture_id,piece_id,phone,lens,zoom,resolution,profile,lighting,tilt_deg,ruler_nominal_mm,ruler_measured_mm,card_sizes_mm,instrument,instrument_resolution_mm,repeat_measurements,notes
```

CSV de comparação: uma linha por captura/métrica, com ponto decimal:

```csv
capture_id,scanner_json,metric,physical_value
phoneA-001,phoneA-001.json,width_mm,400.2
phoneA-001,phoneA-001.json,height_mm,599.8
```

```sh
easyon-scan measure photo.jpg --out results/phoneA-001.dxf --preview results/phoneA-001.png > results/phoneA-001.json
python scripts/real_photo_report.py physical.csv --scanner-dir results --out errors.json
```

Compare o envelope de toda a captura, não peças individuais. Métricas aceitas:
width_mm, height_mm, area_mm2. Erro assinado = scanner − físico; erro relativo =
100 × erro / físico. O JSON separa métricas/unidades e calcula média absoluta,
p95 linear e máximo apenas de comparações válidas. Rejeições e arquivos ausentes
ficam explícitos, nunca como erro zero. CSV vazio, duplicatas captura/métrica e
valores físicos não positivos ou não finitos são recusados.

## EN

**Real-photo accuracy has not been validated.** This protocol collects observed
comparisons; marker residuals are not physical accuracy estimates.

1. Identify pieces and captures. Independently measure flat paper at least twice;
   record instrument, resolution and repeat spread. Free-layout width/height are
   the short/long sides of the minimum bounding rectangle; fixed layout uses its
   axes. Area requires an independent reference, subtracting holes.
2. Print at actual size and verify each 90 mm black square. Include an independent
   control ruler with a known 100 mm interval, visible in the same plane. Record
   nominal and measured length. Use a dark ruler on the dark table, outside the
   pattern and clear of cards; light objects can become false pieces. Inspect
   the ruler manually: the scanner does not detect or use it for calibration.
3. Keep paper and four or more distinct cards flat on a dark matte table. Surround
   the whole pattern with cards and visible gaps; separate pieces by at least
   3 mm. Avoid shadows, glare and ultrawide lenses.
4. Photograph from above with complete cards and pieces. Keep original files,
   EXIF and resolution. Record phone, lens, zoom, lighting, approximate tilt and
   calibration profile. Repeat after repositioning and at different distances
   and tilts; keep rejected captures too.
5. Save JSON, DXF and preview per capture; inspect contours, holes and symbols.
   Record reasons for exclusions and report sample counts by device/condition.
   Never silently discard errors.

Use the recording-sheet columns, comparison CSV and commands above. Comparisons
refer to the whole capture envelope, not individual pieces. Use decimal points.
Supported metrics: width_mm, height_mm, area_mm2. Signed error is scanner minus
physical; relative error is 100 × signed error / physical. JSON groups metrics
and units separately, reporting mean absolute, linear p95 and worst absolute
error for valid rows only. Rejected scans and unavailable files remain explicit,
never zero errors. Empty CSVs, duplicate capture/metric rows and nonpositive or
nonfinite physical values fail. Observed errors do not establish general accuracy.
