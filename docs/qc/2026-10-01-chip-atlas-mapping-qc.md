# ChIP-seq / ATAC-seq の「mapping rate」低値の原因調査（2026-10-01）

対象は zenigoke カタログの ChIP-seq 90 件と ATAC-seq 36 件（計 126 experiment、うち 1 件はパイプライン失敗）である。
Bisulfite-seq は別調査のため扱わない。
パイプライン、`scripts/`、`db/kknmsmd.db`、`report/`、`/data1/zenigoke/output` には一切手を加えていない。
作業ファイルはすべて `/data1/zenigoke/tmp/qc-20261001-chip/` に置いた。
試料ごとの数値は、この報告と同じディレクトリの [`2026-10-01-chip-atlas-qc.tsv`](2026-10-01-chip-atlas-qc.tsv) にある。

## 結論

Web に表示している「mapping rate」は、アラインメント率ではない。
上流パイプラインが stats.tsv の 7 列目に書く値は「重複除去後に残った mapped レコード数 ÷ fastp 通過リード数」であり、本報告ではこれを **usable rate** と呼ぶ。
「duplication rate」は `100 − usable rate` として計算されており、未マップと重複を合算した値である。
カタログ DB はこの 2 つをそのまま mapping_rate と duplication_rate に入れ、さらに列名のずれで reads_filtered に生リード数、reads_mapped に fastp 通過リード数を入れている。

ENA から各 run の先頭 10 万スポットを取り直し、本番と同じコンテナと参照で再アラインメントした結果、真のアラインメント率の中央値は 89.2% だった（表示値の中央値は 62.6%、差の中央値は 17.4 ポイント）。
表示値が 50% 未満の 45 試料のうち、32 試料は「アラインメントされないリード」が損失の主因で、13 試料は重複が主因だった。

原因を、説明できる試料数の多い順に並べると次のとおり。

1. **指標の定義と列名の誤り**（全 125 試料、検証済み）：表示値は重複除去後の値で、アラインメント率より系統的に低い。
2. **Marchantia 以外の DNA の混入**（50 試料で fastp 通過リードの 10% 以上、検証済み）：細菌様 DNA（45 試料で 10% 以上）とヒト DNA（24 試料で 10% 以上）。表示値 50% 未満のうち 32 試料の主因であり、PRJNA690531、PRJNA641427、PRJNA740326、PRJNA597314 の ChIP と、PRJNA1289709 の H3K27me3 と H2Aub ChIP に集中する。
3. **PCR 重複（低ライブラリ複雑度）**（推定重複率 50% 以上が 12 試料、全量の値からの推定）：PRJNA740326 の 4 試料（67〜75%）、PRJNA597314 の IgG 1 件、PRJNA641427 と PRJNA1043823 の ChIP 各 1 件、ATAC 5 件（PRJNA1226525 の 4 件と PRJNA1043823 の 1 件）。ATAC の重複の多くはオルガネラ由来リードである（4.3 節）。
4. **参照ゲノムに葉緑体とミトコンドリアが含まれていない**（オルガネラ由来リードが 20% 以上の試料は 24、うち ATAC 18、ChIP input 6）：PE ではオルガネラ由来リードの約 84%（中央値）が核ゲノム上の NUPT/NUMT（核に転移したオルガネラ配列）に高い MAPQ で置かれる。
   その結果、オルガネラ由来リードは重複として除かれて表示値を下げるだけでなく、偽ピークを作る。
   全 36 件の ATAC で q-value 上位 100 ピークのうち 74 以上がこの領域に入っており、input 4 件では上位 100 ピークがすべてこの領域にある。

棄却した仮説は、PE を SE として処理したことによる損失、性染色体（U/V）の欠落、短い断片長、アダプター残り、品質不良、spike-in ゲノム（大腸菌、phiX、lambda）、複数 run の取りこぼしである（4.6 節）。

## 1. 方法

### 1.1 既存出力の読み取り

試料ごとに次の 4 つを突き合わせた。

- `/data1/zenigoke/output/{chipseq,atacseq}/{ACC}/{ACC}.stats.tsv`
- 同ディレクトリの `{ACC}_fastp.json`（本番実行時の fastp レポート）
- `/data1/zenigoke/logs/{ACC}.log`（`Mapped reads (post-dedup): N` 行を含む）
- `db/kknmsmd.db` の `sample` と `sample_curation`（python3 の sqlite3 で読んだ）

stats.tsv の各列の意味は、`~/repos/chip-atlas-pipeline-v2/scripts/pipeline-v2.sh` の Step 5 で決めた。
この計算式は 2026-04-24 のコミット 8e715ee 以降変わっておらず、zenigoke の実行（2026-04-27 以降）にも同じ式が使われている。
ログの `Step 4: Writing statistics TSV` という表記から、実行時のスクリプトは multiBigwigSummary 導入前の版だとわかるが、stats の式は同じである。

### 1.2 サブサンプルの再アラインメント

本番の FASTQ と BAM は削除済みで、アラインメント前の数値は残っていない。
そこで各 run の FASTQ の先頭 10 万スポット（PE は 10 万ペア = 20 万リード、SE は 10 万リード）を ENA から取得した。
SRX22603363 だけは ENA が `_1.fastq.gz` をディレクトリとして返したため、NCBI から `fastq-dump -X 100000` で取得した。

取得したリードは、本番と同じコンテナ `ghcr.io/inutano/chip-atlas-pipeline-v2:v1.0.0`（fastp 1.3.1、bwa-mem2 2.2.1、samtools 1.23.1）で処理した。
fastp は本番と同じく既定値、bwa-mem2 は `mem -K 10000000`（PE は `-p`）とした。
参照は 2 種類使った。

- **本番参照**：`/data1/zenigoke/references/MpTak_v7.1/MpTak_v7.1.fa`（chr1〜8、chrU、chrV）
- **拡張参照**：本番参照に葉緑体（AP025455.1）、ミトコンドリア（AP025456.1）、phiX174（NC_001422.1）、大腸菌 K-12（NC_000913.3）、lambda（NC_001416.1）を加えたもの。葉緑体とミトコンドリアは MarpolBase が README で案内している GCA_039105155.1（Tak-1 + chrU + MT + CP）の構成配列である。

本番参照に乗らなかったリード（計 559 万リード）はまとめて GRCh38（GATK bundle の bwa-mem2 index）に SE でマップし、リード長の 80% 以上が整列し NM がリード長の 5% 以下のものをヒト由来と数えた。
さらに試料群ごとに未マップリードを 15〜30 本ずつ無作為に選び、NCBI BLAST（megablast、core_nt）に計約 550 本を投げた。
megablast で no hit だったリードの一部は blastn で再検索した。

### 1.3 指標の算出

サブサンプルから得た割合と、本番ログの重複除去後レコード数を組み合わせて全量の値を出した。

- **aligned_pct**：fastp 通過リードのうち、本番参照に primary として整列した割合（サブサンプル）
- **unique_or_filtered_pct**：同じく MAPQ 30 以上で整列した割合
- **dup_pct**：本番ログの重複除去後 mapped レコード数 M、fastp 通過リード数 R、サブサンプルの整列率 a と supplementary 比率 s から、`1 − M / (R × (a + s))` で推定した重複率
- **usable_pct**：`M / R`。表示中の mapping_rate と同じ値になる
- **organelle_pct**、**human_pct**、**other_unmapped_pct**：拡張参照で葉緑体とミトコンドリアに乗った割合、GRCh38 に乗った割合、どちらにも乗らなかった割合

dup_pct の式は、`samtools view -c -F 4` が supplementary を含めて数えること（2 節）を補正している。
サブサンプルの `samtools markdup -f` で、重複除去後の `-F 4` 件数が「primary mapped + supplementary − 重複」に一致することを確認した（SRX7548551 で 141,000 = 144,662 − 3,662）。

サブサンプルはファイル先頭のリードなので、フローセルの最初のタイルに偏る。
アラインメント率と混入率の推定には十分だが、重複率は全量の値から間接的に出しているため数ポイントの誤差を含む。

## 2. stats.tsv と DB の各列が実際に意味するもの

pipeline-v2.sh の Step 5 は次の 15 列を書く。

| 列 | pipeline-v2.sh での中身 | zenigoke の解釈（`build_report._chipseq_stats_fields`） | DB 列 |
|---|---|---|---|
| 1 | SAMPLE_ID | sample | accession |
| 2 | PE_FLAG（0 = SE、1 = PE） | layout | layout（正しい） |
| 3 | FASTQ バイト数（PE は合計） | fastq_size | fastq_size |
| 4 | 固定値 `"0"`（v1 互換の空欄） | reads_raw | なし |
| 5 | fastp の before_filtering.total_reads（PE は R1+R2 の合計） | reads_filt | **reads_filtered**（実際は生リード数） |
| 6 | fastp の after_filtering.total_reads | reads_mapped | **reads_mapped**（実際は fastp 通過リード数） |
| 7 | `MAP_RATE = MAPPED / READS_AFTER × 100` | mapping_rate | **mapping_rate**（実際は usable rate） |
| 8 | `DUP_RATE = (1 − MAPPED / READS_AFTER) × 100` | duplication_rate | **duplication_rate**（実際は 100 − usable rate） |
| 9〜11 | dedup BAM、bedGraph、BigWig のバイト数 | 同左 | なし |
| 12〜14 | q05、q10、q20 のピーク数 | 同左 | sample_chipseq |
| 15 | 経過分 | elapsed_min | elapsed_min |

ここで `MAPPED = samtools view -c -F 4 dedup.bam` であり、dedup.bam は `samtools markdup -r` で重複を除いた後の BAM である。
`-F 4` は未マップだけを除くので、supplementary アラインメントも数に入る。
PE では R1 と R2 を別レコードとして数えるので、分母（fastp の total_reads も R1+R2）と単位はそろっている。

この対応は全 125 試料で検証した。
DB の reads_filtered、reads_mapped、mapping_rate はすべて stats.tsv の 5、6、7 列と一致し、7 列目はログの `Mapped reads (post-dedup)` から再計算した値と丸め誤差の範囲で一致した。
fastp の before_filtering.total_reads は全試料で ENA の read_count × 2（PE）または × 1（SE）に一致したので、ダウンロードの欠落はない。
`mapping_rate + duplication_rate = 100` は式の構造から必ず成り立つ。

列名のずれは、`_chipseq_stats_fields` のコメントにある「mapping_rate 以降がずれていたので reads_mapped を挿入した」修正に由来する。
この修正で 7 列目以降の位置は合ったが、4〜6 列目の名前が 1 列ずつ前にずれたまま残った。
テスト（`tests/test_build_report.py`）の stats.tsv 例も、4 列目に 10000000 を置いた架空の形式で書かれており、上流の実際の出力（4 列目は常に `0`）を反映していない。

具体例として SRX9800303 を挙げる。
DB と Web の表示は reads_filtered = 126,003,842、reads_mapped = 119,369,300、mapping_rate = 9.6%、duplication_rate = 90.4% である。
実際には生リードが 126,003,842、fastp 通過が 119,369,300、重複除去後の mapped レコードが 11,480,297（ログ）である。
サブサンプルでの真のアラインメント率は 12.9%、推定重複率は 26.6% で、残りの 86% は細菌様 DNA だった（4.2 節）。
表示の「duplication rate 90.4%」のうち約 87 ポイントは混入による未マップで、重複によるものは約 3 ポイントにすぎない。

依頼文にあった SRX15503703 の例も同じ構造である。
`53268038 / 52973920 / 21.3 / 78.7` は生リード、fastp 通過、usable rate、100 − usable rate であり、真のアラインメント率は 67.7%、推定重複率は 68.9% だった。

ATAC も同じ式で計算されており、事情は同じである。

## 3. 全体像

| BioProject | 種別 | n | 表示値 中央値（範囲） | 真の aligned% 中央値（範囲） | dup% 中央値 | ヒト% 中央値 | 細菌様% 中央値 | オルガネラ% 中央値 | PASS/WARN/FAIL |
|---|---|---|---|---|---|---|---|---|---|
| PRJNA740326 | ChIP | 4 | 20.0（17.6–21.3） | 68.4（62.6–69.7） | 70.9 | 4.9 | 25.4 | 2.3 | 0/4/0 |
| PRJNA690531 | ChIP | 24 | 22.2（9.6–78.5） | 28.1（12.9–92.9） | 20.2 | 0.2 | 41.6 | 0.3 | 3/7/14 |
| PRJNA641427 | ChIP | 10 | 29.9（21.6–40.8） | 52.8（31.5–71.2） | 42.9 | 12.3 | 34.4 | 1.7 | 0/10/0 |
| PRJNA597314 | ChIP | 6 | 44.2（26.6–64.7） | 74.6（50.9–92.3） | 36.2 | 9.2 | 14.8 | 0.2 | 1/5/0 |
| PRJNA1043823 | ATAC | 2 | 53.3（49.4–57.3） | 96.4（96.1–96.7） | 47.7 | 0.0 | 0.4 | 57.4 | 0/2/0 |
| PRJNA1043823 | ChIP | 6 | 61.5（47.0–77.2） | 99.1（93.0–99.7） | 38.0 | 0.0 | 0.8 | 0.3 | 4/2/0 |
| PRJNA1226525 | ATAC | 32 | 65.0（41.4–79.2） | 93.0（78.8–97.7） | 28.1 | 0.0 | 1.6 | 19.8 | 15/17/0 |
| PRJNA1289709 | ChIP | 39 | 79.9（31.1–91.4） | 94.5（33.6–99.6） | 19.9 | 1.3 | 1.0 | 1.4 | 24/15/0 |
| PRJNA597314 | ATAC | 2 | 90.7（90.4–91.0） | 99.7（99.7–99.7） | 9.4 | 0.0 | 0.0 | 4.1 | 2/0/0 |

割合はすべて fastp 通過リードに対する値で、PRJNA1289709 は失敗した SRX29617452 を除く 39 件である。
PASS/WARN/FAIL の基準は 6.3 節に示す。

表示値が 50% 未満の 45 試料について、`100 − 表示値` を「未整列」（100 − aligned_pct）と「重複」（aligned_pct − usable_pct）に分けると、未整列が大きい試料が 32、重複が大きい試料が 13 だった。

- 未整列が主因：PRJNA690531 の H2Aub と H3K27me3 の 16 試料、PRJNA641427 の 10 試料のうち 9 試料、PRJNA1289709 の H3K27me3 の 4 試料、PRJNA597314 の 3 試料
- 重複が主因：PRJNA740326 の 4 試料、PRJNA1226525 の ATAC 5 試料（SRX27755071、072、075、076、080）、SRX22603363、SRX22603367、SRX7548551、SRX8604218

## 4. 原因ごとの所見

### 4.1 指標の定義と列名の誤り（全 125 試料、検証済み）

2 節のとおり、表示中の mapping_rate は重複除去後の値である。
真のアラインメント率との差の中央値は 17.4 ポイント（最小 0.4 ポイント）で、どの試料でも表示値は真の値より低い。
「duplication rate」は未マップ分を含むので、混入の多い試料では重複がほとんどなくても 80〜90% と表示される（SRX9800303 は推定重複率 26.6% に対し表示 90.4%）。
DB の reads_mapped には fastp 通過リード数が入っており、SRX9800303 の試料ページには「reads_mapped 119,369,300」と、重複除去後に残った mapped レコード（11,480,297）の約 10 倍の値が表示されている。

この誤りだけでは「低い試料がある」ことは説明できない。
真のアラインメント率が低い試料は実在し、それを 4.2〜4.4 節で扱う。

### 4.2 Marchantia 以外の DNA の混入（50 試料、検証済み）

本番参照にも拡張参照にも乗らないリードを、GRCh38 へのマップと BLAST で調べた。
fastp 通過リードの 10% 以上が外来 DNA（ヒト + 細菌様）である試料は 50 あり、ヒト 10% 以上が 24、細菌様 10% 以上が 45 だった。

BLAST の結果は次のとおり（試料群ごとに 15〜30 本）。

- PRJNA690531 の H3K27me3（SRX9800298、301、302、305 の計 85 本）：細菌 43 本、no hit 42 本、ヒト 0 本。属は Microbacterium、Sphingomonas、Bradyrhizobium、Sphingobium、Cellulomonas など。megablast で no hit のリードを blastn で再検索すると、14 本中 11 本が Sphingomonas、Leifsonia、Janthinobacterium、Mycolicibacterium などに同一性 75〜85% でヒットした。単一の spike-in 生物ではなく、配列データベースに近縁株がない細菌を含む混合群集である。
- PRJNA690531 の H2Aub（SRX9800290、292 の計 50 本）：ヒトとチンパンジー 18 本、細菌 17 本、no hit 12 本。
- PRJNA641427（WRKY-tagRFP ChIP）：Burkholderia 属が多く、ヒトなどの哺乳類配列と no hit が混じる。
- PRJNA1289709 の H3K27me3（SRX29617456、470）：40 本中 31 本がヒト（チンパンジー 1 本を含む）。
- PRJNA597314 の IgG と TCP1：Cutibacterium acnes、Staphylococcus epidermidis などの皮膚常在菌とヒト、植物形質転換用バイナリーベクター（pCambia1300 系など）。
- PRJNA1226525 の ATAC（SRX27755080、099）：Stenotrophomonas geniculata が 20 本中 10〜11 本。残りはオルガネラ配列（Marchantia や他の苔類の葉緑体とミトコンドリア）。

GRCh38 に乗ったリードは主染色体全体に散らばり、rDNA コンティグやヒト mtDNA への偏りはなかった。
ヒト由来の多い 6 試料（SRX9800292、295、SRX29617456、464、470、SRX7548550）では主染色体が 98.9〜99.6%、MAPQ 30 以上が 87〜93% だった。
例外は SRX8604216 で、19% が rDNA を含むコンティグ（chr22_KI270733v1_random）に乗った。
したがってヒト由来リードは、保存性の高い rDNA などへの誤マップではなく、ヒトのゲノム DNA そのものである。

混入は、抗体（IP の標的）ごとに偏っていた。

| BioProject | IP 標的（タイトルから判断） | n | aligned% | ヒト% | 細菌様% |
|---|---|---|---|---|---|
| PRJNA690531 | H3 | 8 | 86.6 | 0.0 | 13.2 |
| PRJNA690531 | H2Aub | 8 | 27.4 | 29.6 | 41.6 |
| PRJNA690531 | H3K27me3 | 8 | 16.4 | 0.2 | 82.8 |
| PRJNA1289709 | H3K27me3 | 4 | 41.9 | 43.3 | 10.6 |
| PRJNA1289709 | H2AKUb | 4 | 59.5 | 30.9 | 6.7 |
| PRJNA1289709 | H3K4me3 | 4 | 93.8 | 4.5 | 1.3 |
| PRJNA1289709 | H3、H2A 変異体（H2A.M、H2A.X、H2A.Z）、H3K27me1、H3K9me1 | 20 | 90.6〜99.6 | 0.2〜5.2 | 0.1〜2.1 |
| PRJNA1289709 | Input | 7 | 91.5 | 0.1 | 0.6 |
| PRJNA641427 | WRKY-tagRFP、tagRFP、Tak-1 | 10 | 52.8 | 12.3 | 34.4 |
| PRJNA740326 | HSFA1V-tagRFP、Tak-1 | 4 | 68.4 | 4.9 | 25.4 |
| PRJNA597314 | IgG | 3 | 70.7 | 12.2 | 16.9 |
| PRJNA597314 | MpTCP1 | 3 | 81.6 | 6.1 | 12.1 |

同じ研究の input や H3 では混入が少なく、IP の回収量が少ないと考えられる標的（H3K27me3、H2Aub、転写因子、IgG）ほど混入が多い。
この偏りから、混入源は植物体そのもの（表面や内生の細菌）よりも、試薬や実験環境に由来する少量の DNA である可能性が高いと考える。
回収 DNA が少ない IP では、試薬由来の DNA（いわゆる kitome）やヒト DNA が相対的に大きな割合を占めやすい。
PRJNA690531 は低インプット用キット（Ovation Ultralow System V2）を使っており、細菌の属構成（Microbacterium、Sphingomonas、Bradyrhizobium、Ralstonia、Burkholderia）は試薬混入としてよく報告される属と重なる。
ただしこれは推定であり、確認はしていない。
ヒト DNA が H3K27me3 と H2Aub の IP にだけ多く出る点は、ヒトクロマチンを spike-in として加えた可能性とも整合するが、GEO と SRA のメタデータに spike-in の記載はなく、こちらも確認できない。

混入リードは本番参照にはほとんど乗らない。
ヒト chr1 と大腸菌ゲノムから切り出した 150 bp 断片（各 5 万本と 4 万本）を本番参照にマップすると、整列したのは 1.1% と 0.2% で、すべて 120 bp 未満の部分整列だった（MAPQ 30 以上は 0.07% と 0.10%）。
したがって混入がピーク位置を直接ゆがめる可能性は低い。
影響は主に有効リード数の減少であり、PRJNA690531 の H3K27me3 群（SRX9800298〜305）は 0.9 億〜1.6 億リードを読んでも、有効断片は 570 万〜1,100 万にとどまる。

### 4.3 PCR 重複と低ライブラリ複雑度（12 試料、全量の値からの推定）

推定重複率が 50% 以上の試料は 12 あった。

- PRJNA740326 の 4 試料（SRX15503703〜706）：67〜75%。いずれも 5,300 万〜6,400 万リードを読んで有効断片は約 550 万で、ライブラリの多様性が上限に達している。fastp の重複推定値（37〜58%）も高い。
- PRJNA597314 の IgG（SRX7548551）：66%。IgG コントロールとして想定の範囲内である。
- PRJNA641427 の SRX8604217（51%）、PRJNA1043823 の SRX22603363（52%）と ATAC の SRX22603367（51%）。
- PRJNA1226525 の ATAC 4 試料（SRX27755071、075、076、080 が 51〜55%）。境界の SRX27755072 も 49.5% である。

ATAC の重複の多くはオルガネラ由来である。
サブサンプル（20 万リード）で `samtools markdup` をかけると、SRX27755071 ではオルガネラ由来リードの重複率が 24.7%、核由来が 12.6% で、全重複の 62.5% がオルガネラ由来だった（SRX27755076 では 76.8%）。
オルガネラ由来リードは核ゲノム上の 0.13%（約 31 万塩基）しかない NUPT/NUMT に集まるので、全量では位置がすぐに飽和し、大半が重複として除かれる。
一方 PRJNA740326 ではオルガネラ由来の重複は 3.9% にすぎず、真に低複雑度のライブラリである。

### 4.4 参照にオルガネラゲノムがないこと（24 試料でオルガネラ由来 20% 以上、偽ピークは 31 試料以上、検証済み）

MpTak_v7.1 標準ゲノム（`MpTak_v7.1.fa`）は chr1〜8、chrU、chrV の 10 配列だけで、葉緑体とミトコンドリアを含まない。
`scripts/prepare-marchantia.sh` もこのファイルだけを取得している。

拡張参照でオルガネラに乗ったリードは、ATAC で 3.9〜61%（PRJNA1226525 の中央値 19.8%、PRJNA1043823 の 2 件は 54% と 61%）、ChIP の input で 7〜38%（6 件が 32〜38%）だった。
本番参照では、これらのリードは未マップになるより核ゲノム上に置かれることが多い。
拡張参照と本番参照のアラインメントをリードごとに突き合わせると、PE 試料（オルガネラ 5% 以上の 43 試料）ではオルガネラ由来リードの 81〜95%（中央値 84%）が核ゲノムに置かれ、個別に確かめた PE 3 試料では、核に置かれたリードの 82〜84% が MAPQ 30 以上だった（SRX29617483 では 58,893 本中 48,086 本）。
50 bp SE の 4 試料では約半分が核に置かれ、残りは未マップになった。

これらのリードが集まる場所は、葉緑体とミトコンドリアのゲノムを 150 bp ずつ 10 bp 刻みで切り出して本番参照にマップすると再現できる。
100 bp 以上整列した断片をまとめると 1,278 領域、計 311,657 bp（ゲノムの 0.13%）になり、上位は chr5:14.03 Mb、chr5:17.09 Mb、chr8:10.68 Mb、chr8:22.84 Mb、chr6:27.81 Mb などで、試料のリードが集中する場所と一致した。
この領域（前後 500 bp を加えて 1.45 Mb、ゲノムの 0.58%）を「オルガネラ様領域」とし、本番の q05 ピークとの重なりを全試料で数えた。

- ChIP input 7 件：ピークの 56〜72% がオルガネラ様領域にあり、PE の 4 件（SRX29617468、469、482、483）は q-value 上位 100 ピークがすべてこの領域にある。
- PRJNA641427 の WRKY ChIP 10 件：ピークの 24〜66% がこの領域にある。
- PRJNA1289709 のヒストン ChIP：H2A.X、H3K27me1、H3K9me1、H3K27me3 などピーク数が数百の試料で 21〜66%。
- ATAC 36 件：ピーク全体に占める割合は 1.8〜8.8% だが、q-value 上位 100 ピークのうち 74〜100 本（25 件では 90 本以上）がこの領域にある。

つまり、オルガネラ DNA が参照にないことは表示値を下げるだけでなく、input でも ChIP でもピークを作る。
ATAC で「最も強いピーク」として表示されているものの多くは、クロマチンの開き具合ではなく葉緑体 DNA の誤配置である。

### 4.5 ATAC の短い断片（PRJNA1226525、低値の原因としては棄却）

PRJNA1226525 の ATAC は fastp の挿入長ピークが 44 bp で、断片の 20〜52% が 50 bp 未満、トリミング後の平均リード長は 53〜90 bp だった。
しかしサブサンプルではトリミング後に 30 bp 未満となるリードはほぼなく、真のアラインメント率は 78.8〜97.7% と高い。
短い断片は MAPQ をいくらか下げる（MAPQ 30 以上は 72〜91%）が、表示値の低さの主因ではない。
主因はオルガネラ由来リードとその重複（4.3、4.4 節）と、一部試料の細菌混入である。
SRX27755079〜084 と 096〜102 は 7〜19% が拡張参照にもヒトにも乗らず、BLAST で調べた 2 試料（SRX27755080、099）ではその大半が Stenotrophomonas geniculata だった。

### 4.6 棄却した仮説

- **PE を SE として処理したことによる損失**：SE として処理された 11 試料（SRX29617444〜455、452 は失敗）は ENA と NCBI のどちらでも 1 スポット 50 bp（例：SRR34473250 は 12,957,311 スポット、647,865,550 塩基）で、アーカイブに片側のリードしか存在しない。SE 処理は正しく、これらの真のアラインメント率は 79.8〜99.2% と高い。`retry-pe-fallback.sh` が原因の低値はない。
- **複数 run の取りこぼし**：ChIP と ATAC の 126 experiment はすべて 1 run だった。ただし `run-sample.sh` は run ごとに `fast-download.sh` を呼んで `*_1.fastq.gz` の先頭 1 つだけを使うので、複数 run の experiment では 2 本目以降を捨てる潜在的な不具合がある（BS-seq 側で確認が必要）。
- **性染色体と系統**：参照は Tak-1 の常染色体と chrV、Tak-2 の chrU を含むので、雌雄どちらの試料もゲノム全体を受け止められる。chrU と chrV に乗る割合は試料群で逆転しており（Tak-1 の input 7 件は chrU 0.8〜1.4%、chrV 1.9〜2.7%。PRJNA641427 は chrU 2.4〜9.5%、chrV 0.4〜1.7%。SRX9800284〜287 は chrU 5.6〜6.5%、chrV 0.5〜0.6%）、後二者は雌系統の可能性がある（ChIP の濃縮による偏りもありうるので推定にとどまる）。いずれにせよ両方の性染色体が参照にあるので、未整列の原因にはならない。PRJNA690531 は Tak-1 ではなく ecotype「Upp」だが、同じ研究の H3 ChIP は 79〜93% 整列しており、系統差の影響は小さい。
- **spike-in ゲノム**：拡張参照で大腸菌 K-12 は最大 3.0%（PRJNA1289709 の H3K27me3 と H2AKUb で 1.4〜3.0%）、phiX は最大 0.7%、lambda はほぼ 0% だった。いずれも表示値の低さを説明する量ではない。ChIP-Rx 型の Drosophila spike-in を示す BLAST ヒットもなかった。
- **アダプターと品質**：fastp 通過率は 88〜100% で、低いのは PRJNA597314 の 4 試料（88〜91%、低品質リード除去）だけである。アダプター残りによる未整列は見られなかった。

### 4.7 IgG と input

IgG（SRX7548551〜553）は外来 DNA 21〜49% と重複 30〜66% が重なり、表示値は 27〜50% である。
IgG は回収量が少ないので、この程度の混入と重複はコントロールとして予想の範囲内である。
input 7 件はヒト DNA がほぼ 0%（0.06〜0.42%）だが、6 件はオルガネラ由来リードが 32〜38% あり（SRX29617455 は 7% で、代わりに細菌様が 12%）、そのせいでピークを 834〜2,740 個持つ（4.4 節）。
input をコントロールに使う解析では、オルガネラ様領域を除外しないと偽のエンリッチメントや逆に偽の欠落が出る可能性がある。

### 4.8 失敗試料 SRX29617452

カタログには「SRA の FASTQ が壊れている可能性」と書かれているが、これは誤りである。
`/data1/zenigoke/fastq/SRX29617452/SRR34473242.fastq.gz` の md5 は ENA の fastq_md5（8e3321024678de84254a62b7169bde4b）と一致し、39,154,915 レコードすべてで SEQ と QUAL の長さが一致した。
先頭 10 万リードは 97.1% 整列した。
エラーは `samtools sort` が bwa-mem2 の出力を読んだときの `SEQ and QUAL are of different length` であり、fastp から bwa-mem2 への流れの中で発生している。
同じコンテナと同じ FASTQ で、本番と同じ fastp から bwa-mem2 への流れを 10 スレッドで再実行したところ、出力 38,747,624 レコードのすべてで SEQ と QUAL の長さが一致し、エラーは再現しなかった。
本番の 2 回の失敗（2026-05-01 の 00:46 と 11:37 開始）はどちらも `THREADS=32`（bwa-mem2 は 31 スレッド）で動いていた。
スレッド数に依存する bwa-mem2 側の不具合か、実行時の一時的な問題の可能性があるが、32 スレッドでの再現はスレッド上限（12）のため試していない。
`THREADS=12` などで再実行すれば通る可能性が高く、カタログの説明文は「FASTQ の破損」から「パイプライン実行時のエラー（原因未特定、FASTQ は正常）」に直すべきである。

## 5. 問題のある試料

### 5.1 FAIL（15 試料）

| 試料 | 内容 | 主な理由 |
|---|---|---|
| SRX9800291〜297 | PRJNA690531 H2Aub ChIP（7 件） | 整列 22〜29%。ヒト 23〜36% と細菌様 36〜53% |
| SRX9800298〜304 | PRJNA690531 H3K27me3 ChIP（7 件） | 整列 13〜21%。細菌様 79〜86% |
| SRX29617452 | PRJNA1289709 H2A.M.2 rep3 | パイプライン失敗（4.8 節） |

SRX9800290（H2Aub、整列 33.4%）と SRX9800305（H3K27me3、30.2%）は基準のわずかに上で WARN になったが、性質は同じ群である。
PRJNA690531 の FAIL 試料も有効断片は 500 万〜930 万あり、ピークも 6,000〜11,000 個呼ばれている。
FAIL は「データが使えない」ことではなく、「リードの 7 割以上が Marchantia 由来でない」ことを示す。

### 5.2 注意が必要な WARN

- PRJNA641427 の全 10 件：整列 31〜71%、外来 DNA 28〜68%、ピークの 24〜66% がオルガネラ様領域
- PRJNA740326 の全 4 件：重複 67〜75%、細菌様 25〜30%
- PRJNA1289709 の H3K27me3 4 件（SRX29617456、457、470、471）と H2AKUb 4 件（SRX29617464、465、478、479）：ヒト 15〜54%
- PRJNA597314 の IgG 3 件と TCP1 2 件（SRX7548550、554）：外来 DNA 18〜49%
- ChIP input 7 件と ATAC のオルガネラ高値試料：オルガネラ由来 20〜61%、ピークのかなりの部分がオルガネラ様領域

### 5.3 キュレーションの不一致（副次的な発見）

IP 標的のキュレーション値が実験タイトルと食い違う試料が 11 件あった。
SRX29617466、480、481（タイトルは H3、キュレーションは H2A）、SRX8604219（WRKY14-RFP、キュレーションは tagRFP）、SRX9800283、284、286、287（H3、キュレーションは H3K27me3、H2Aub、H2AK119ub、H2AK119R）、SRX9800300、302、303（H3K27me3、キュレーションは H2AK119ub、H2AK119R）である。
PRJNA690531 では遺伝子型名（H2AK119R など）が抗体として入っている。
TSV の `target_from_title` 列にタイトルから読んだ標的を載せた。

## 6. 推奨

### 6.1 パーサーと表示を直す（再実行不要）

DB の列の意味を正しくし、Web の表示ラベルを変える。
次の差分を提案する（適用はしていない）。

```diff
--- a/scripts/build_report.py
+++ b/scripts/build_report.py
@@ def _chipseq_stats_fields(row: list[str]) -> dict[str, str]:
-    # Upstream pipeline-v2.sh stats.tsv is 15 contiguous columns. The plan's
-    # original key list missed the `reads_mapped` count between `reads_filt`
-    # and `mapping_rate`, which made every column from `mapping_rate` onward
-    # land on the wrong key. Verified empirically against SRX29617444 stats.
+    # Upstream pipeline-v2.sh (Step 5) writes 15 columns:
+    #   id, PE flag, FASTQ bytes, literal "0" (v1 placeholder),
+    #   fastp total_reads BEFORE filtering, fastp total_reads AFTER filtering,
+    #   MAP_RATE = 100 * (samtools view -c -F 4 on the markdup -r BAM) / reads_after
+    #   DUP_RATE = 100 - MAP_RATE, dedup BAM / bedGraph / bigWig bytes,
+    #   peaks q05/q10/q20, elapsed min.
+    # MAP_RATE is the non-duplicate aligned fraction ("usable rate"), NOT the
+    # alignment rate. DUP_RATE lumps unmapped + duplicate reads; it is NOT a
+    # duplication rate.
     keys = [
-        "sample", "layout", "fastq_size", "reads_raw", "reads_filt",
-        "reads_mapped", "mapping_rate", "duplication_rate",
+        "sample", "layout", "fastq_size", "v1_placeholder", "reads_raw",
+        "reads_filt", "mapping_rate", "non_usable_rate",
         "dedup_bam_size", "bedgraph_size", "bigwig_size",
         "peaks_q5", "peaks_q10", "peaks_q20", "elapsed_min",
     ]
--- a/scripts/build_catalog_db.py
+++ b/scripts/build_catalog_db.py
@@ def parse_chipseq_stats(row: List[str]) -> Dict[str, Any]:
     fields = _chipseq_fields_fn(row)
+    reads_filt = _safe_int(fields.get("reads_filt"))       # fastp-passed reads
+    usable = _safe_float(fields.get("mapping_rate"))        # non-dup aligned / passed
     return {
         "layout": fields.get("layout"),
         "fastq_size": fields.get("fastq_size"),
-        "reads_filtered": _safe_int(fields.get("reads_filt")),
-        "reads_mapped": _safe_int(fields.get("reads_mapped")),
-        "mapping_rate": _safe_float(fields.get("mapping_rate")),
-        "duplication_rate": _safe_float(fields.get("duplication_rate")),
+        "reads_filtered": reads_filt,
+        # non-duplicate aligned records; the exact value is in the run log
+        # ("Mapped reads (post-dedup): N") and should be preferred when present
+        "reads_mapped": (round(reads_filt * usable / 100)
+                         if reads_filt is not None and usable is not None else None),
+        "mapping_rate": usable,      # relabel in UI: "usable (dedup, aligned) %"
+        "duplication_rate": None,    # 100 - usable is not a duplication rate
         "elapsed_min": _safe_float(fields.get("elapsed_min")),
```

`build_catalog_db.py` の import 失敗時のフォールバック鍵リストも同じに直す。
`tests/test_build_report.py` の stats.tsv 例は、4 列目を `0` にした上流の実形式に置き換える。
`build_catalog_pages.py` では、ChIP と ATAC の mapping_rate を「Usable reads (dedup, aligned) %」のように表示名を変え、duplication_rate は表示しない。
生リード数（stats.tsv の 5 列目）を表示したい場合は、DB に reads_raw 列を足す。
`build_report.py` の試料一覧（`cols = ["accession", "mapping_rate", "duplication_rate", ...]`）も duplication_rate を参照しているので、そこからも外す。

### 6.2 本報告の QC 値をカタログに取り込む（再実行不要）

真のアラインメント率と重複率は本番の出力からは復元できない。
本報告の TSV（サブサンプル推定値）を `sample_qc` のような別テーブルとして取り込み、試料ページと一覧に aligned_pct、dup_pct、organelle_pct、外来 DNA%、qc_flag、cause を表示することを勧める。
値がサブサンプルからの推定であることは表示に明記する。

### 6.3 Web に出す QC フラグの基準

単位は fastp 通過リードに対する割合で、有効断片は PE ならレコード数の半分、SE ならレコード数とする。

| 指標 | PASS | WARN | FAIL |
|---|---|---|---|
| aligned_pct（本番参照への整列率） | 70% 以上 | 30〜70% | 30% 未満 |
| dup_pct（推定重複率） | 50% 未満 | 50〜80% | 80% 以上 |
| 有効断片数（重複除去後） | 400 万以上 | 100 万〜400 万 | 100 万未満 |
| 外来 DNA（ヒト + 細菌様） | 10% 未満 | 10% 以上 | （aligned_pct で判定） |
| オルガネラ由来 | 20% 未満 | 20% 以上 | なし |

いずれか 1 つでも FAIL なら FAIL、そうでなく 1 つでも WARN なら WARN とした。
この基準で ChIP は PASS 32、WARN 43、FAIL 15（失敗 1 件を含む）、ATAC は PASS 17、WARN 19 になる。
aligned_pct の 70% は、混入のない試料群（PRJNA1043823 の ChIP、PRJNA1289709 の H3 や input など）の整列率が 90% 以上であることから、その 2 割以上を失った試料を注意とする目安である。
有効断片の閾値は、ENCODE がヒト（約 3.1 Gb）の narrow なヒストン ChIP に求める 2,000 万断片をゲノムサイズ比（約 1/12.5）で縮めた値（約 160 万）を目安に、余裕を持たせて決めた。
ATAC はオルガネラ由来リードが多いのが普通なので、オルガネラの WARN は「データ不良」ではなく「ピーク解釈の注意」として表示するのがよい。

フラグとは別に、ピークの信頼性を示す `peak_flag` を設けた。
q05 ピークが 50 個以上あり、その 20% 以上がオルガネラ様領域にある試料を WARN とし、31 試料が該当する（PRJNA1289709 の 21 件、PRJNA641427 の 10 件）。
ATAC はピーク数が多いので割合では引っかからないが、上位ピークの大半がこの領域にあるため、ATAC の上位ピーク一覧を出す画面ではこの領域を除くべきである。

### 6.4 オルガネラへの対処

短期的には、オルガネラ様領域の BED（`/data1/zenigoke/tmp/qc-20261001-chip/nupt/organelle_like_regions.bed`、1,278 領域）をブラックリストとして、カタログ上のピーク表示、エンリッチメント解析、行列（npz）集計から除外するのがよい。
上流パイプラインを変えずに済む。

中期的には、`prepare-marchantia.sh` で参照に葉緑体（AP025455.1）とミトコンドリア（AP025456.1）を加えた FASTA を作り、ChIP と ATAC を再実行することを勧める。
こうすればオルガネラ由来リードの大部分は本来の場所に乗り、NUPT 上の偽ピークは大きく減ると見込まれる（オルガネラと完全に同一の NUPT では MAPQ 0 のリードが残る）。
ただし上流は BigWig の RPM 正規化の分母にオルガネラ由来リードも含めるので、ATAC では試料間の比較で値がずれる。
再実行後に chrC と chrM のピークを除く後処理をラッパー側で行えばよい。
再実行の計算量は本番実績で ChIP 1,906 分、ATAC 1,021 分（32 スレッド）、FASTQ は約 490 GB である。

### 6.5 混入と重複への対処

混入と重複は再実行では直らない。
PRJNA690531 の H2Aub と H3K27me3、PRJNA641427、PRJNA740326、PRJNA1289709 の H3K27me3 と H2AKUb は、フラグと原因を表示した上で残し、試料をまたぐ比較（エンリッチメント解析など）の既定値からは FAIL を外すのがよい。
外来リードはほとんど Marchantia 参照に乗らないので、ピークそのものよりも感度（有効リード数）が落ちていることが主な影響である。

### 6.6 SRX29617452 の再実行

4.8 節のとおり FASTQ は正常なので、`THREADS=12` 程度で `run-sample.sh` を再実行することを勧める。
カタログの「Known Limitations」にある「corrupted FASTQ in SRA」という説明は、再実行の結果を待たずに直してよい。

### 6.7 上流パイプラインへの提案（適用していない）

真のアラインメント率と重複率は、`samtools markdup` の統計出力で得られる。
`EXAMINED` が primary mapped リード数（重複除去前）、`DUPLICATE PRIMARY TOTAL` が重複と判定された primary リード数である（サブサンプルで、EXAMINED が flagstat の primary mapped と一致することを確認した）。

```diff
--- a/scripts/pipeline-v2.sh
+++ b/scripts/pipeline-v2.sh
@@ Step 1 (paired-end と single-end の両方)
-    | samtools markdup -r - "$DEDUP_BAM"
+    | samtools markdup -r -f "$WORK/markdup.stats" - "$DEDUP_BAM"
@@ Step 5
-if [ "${READS_AFTER:-0}" -gt 0 ] 2>/dev/null; then
-  MAP_RATE=$(awk "BEGIN {printf \"%.1f\", $MAPPED / $READS_AFTER * 100}")
-  DUP_RATE=$(awk "BEGIN {printf \"%.1f\", (1 - $MAPPED / $READS_AFTER) * 100}")
+# markdup -f: EXAMINED = primary mapped reads before dedup,
+#             DUPLICATE PRIMARY TOTAL = primary reads marked as duplicates
+ALIGNED=$(awk -F': ' '$1=="EXAMINED"{print $2}' "$WORK/markdup.stats")
+DUPS=$(awk -F': ' '$1=="DUPLICATE PRIMARY TOTAL"{print $2}' "$WORK/markdup.stats")
+if [ "${READS_AFTER:-0}" -gt 0 ] && [ "${ALIGNED:-0}" -gt 0 ] 2>/dev/null; then
+  MAP_RATE=$(awk "BEGIN {printf \"%.1f\", $ALIGNED / $READS_AFTER * 100}")
+  DUP_RATE=$(awk "BEGIN {printf \"%.1f\", $DUPS / $ALIGNED * 100}")
```

あわせて、4 列目の固定値 `0` の代わりに重複除去後の mapped 数（MAPPED）を書くか、stats.tsv にヘッダー行を付けると、下流での列の取り違えを防げる。
この変更は ChIP-Atlas 本体の stats の意味も変えるので、上流で合意してから入れる必要がある。

## 付録 A. TSV の列

| 列 | 意味 |
|---|---|
| accession、strategy、bioproject、run | experiment、種別、BioProject、run |
| layout | パイプラインでの処理（stats.tsv の 2 列目：PE/SE） |
| ena_layout | ENA の library_layout（`(1file)` は FASTQ が 1 本だけ） |
| antibody | DB の sample_curation.antibody_target |
| target_from_title | 実験タイトルから読んだ IP 標的（ヒューリスティック） |
| raw_reads | fastp 前のリード数（PE は R1+R2） |
| fastp_pass_pct、read_len_after_trim、insert_peak | 本番 fastp.json から |
| aligned_pct | 本番参照への primary 整列率（サブサンプル） |
| unique_or_filtered_pct | MAPQ 30 以上での整列率（サブサンプル） |
| nuclear_aug_pct | 拡張参照で核染色体に乗った割合 |
| dup_pct | 推定重複率（1.3 節の式） |
| usable_reads、usable_fragments、usable_pct | 重複除去後の mapped レコード数（ログ）、断片数、fastp 通過に対する割合 |
| organelle_pct、chloroplast_pct、mito_pct | 拡張参照でオルガネラに乗った割合 |
| human_pct | GRCh38 に乗った割合 |
| other_unmapped_pct | 拡張参照にも GRCh38 にも乗らない割合（BLAST では大半が細菌） |
| displayed_* | 現在の DB と Web の値（reads_filtered、reads_mapped、mapping_rate、duplication_rate） |
| peaks_q05、peaks_in_organelle_like_pct、peak_flag | 本番ピーク数、オルガネラ様領域に入る割合、6.3 節の判定 |
| qc_flag、cause | 6.3 節の判定と、該当した理由 |

## 付録 B. 再現手順

スクリプトはすべて `/data1/zenigoke/tmp/qc-20261001-chip/scripts/` にある。

```bash
D=/data1/zenigoke/tmp/qc-20261001-chip
python3 $D/scripts/collect_base.py                      # stats.tsv / log / fastp.json / DB の突き合わせ → base.tsv
cut -d, -f2 $D/exps.csv | xargs -P 6 -n 1 bash $D/scripts/subsample.sh   # ENA 先頭 10 万スポット
bash $D/scripts/map_list.sh <accession_list> 6          # fastp → bwa-mem2（本番参照と拡張参照）、コンテナ v1.0.0
python3 $D/scripts/summarize.py                         # → submap.tsv
docker run ... bash $D/scripts/human_map.sh             # 未マップリード → GRCh38
python3 $D/scripts/crosstab.py                          # オルガネラ由来リードの本番での置き場所
python3 $D/scripts/nupt.py ACC:chipseq ...              # 試料ごとのホットスポット
bash $D/scripts/organelle_regions.sh                    # オルガネラ様領域 BED（葉緑体とミトコンドリアのタイリング）
python3 $D/scripts/peak_overlap.py                      # 本番 q05 ピークとオルガネラ様領域の重なり
python3 $D/scripts/dup_by_origin.py ACC ...             # サブサンプルでのオルガネラ由来と核由来の重複率
python3 $D/scripts/blast_remote.py q.fa out.tsv         # NCBI BLAST（megablast, core_nt）
python3 $D/scripts/assemble.py && python3 $D/scripts/final_tsv.py out.tsv
```
