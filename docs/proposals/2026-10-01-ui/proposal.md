# zenigoke 公開前 UI 再設計の提案

- 日付：2026-10-01
- 対象：https://inutano.github.io/zenigoke/ （`scripts/build_catalog_pages.py` が生成する静的サイト）
- 成果物：本書、動くモックアップ（`docs/proposals/2026-10-01-ui/mockup/`）、検証時のスクリーンショット（`img/`）
- 制約：本番のコード（`scripts/`、`report/`、`db/`、`deploy/`、`tests/`）は変更していない。

## 0. 要約

現在のサイトは、処理パイプラインの実行記録を読むための作りになっている。
トップページは 2 軸を選ばせるマトリクスである。
ナビゲーションの 9 項目のうち、Summary と Methods は処理の記録で、戦略別の 3 ページは Browse と同じ表をアッセイごとに分けただけのページである。
外部の研究者が「探す、選ぶ、落とす、IGV で見る」をこなすための部品（組み合わせ可能な絞り込み、選択の保持、一括ダウンロード、研究や論文との対応、QC の目印）は、まだない。

さらに、IGV 連携は本番で壊れている。
マトリクスから「Send to IGV」を押すと、`https://zenigoke-catalog-dsc.s3.ap-northeast-1.amazonaws.comoutput/...bw|SRX... bigwig` という存在しない URL がゲノム指定なしで IGV に送られ、画面には「✔ Sent to IGV」と表示される（偽 IGV サーバで受信内容を確認した。1.2 節の C1〜C3）。
MpTak v7.1 のゲノム本体（FASTA、索引、遺伝子モデル）は S3 に置かれておらず、利用者が自分で用意する手段も案内されていない。

提案の骨子は次の 5 点である。

- **トップをデータ検索にする**：ファセット（アッセイ、ChIP 標的、遺伝子型、発生段階、組織、処理、研究、QC）とテキスト検索で 157 実験を絞り込み、結果表から直接カートへ入れる。ENCODE の検索とカートを、157 件規模に合わせて小さくした形である。
- **カートから一括取得する**：URL リスト、再開可能な bash スクリプト、実験メタデータ TSV、ファイル一覧 TSV、IGV セッション XML の 5 形式をブラウザ内で生成する。サーバは不要である。
- **IGV へ確実に渡す**：`http://127.0.0.1:60151/load` に `genome=` 付きで送り、届かなければセッション XML、通常リンクでの送信、ブラウザ内プレビュー（igv.js）に切り替える。
- **ゲノムを S3 に置く**：FASTA と `.fai`、遺伝子モデル（GFF3）、IGV 用ゲノム JSON を `references/MpTak_v7.1/` に置く。合計約 260 MB で、保管費は月 1 セント未満である。それまでの間は、UCSC GenArk が配る同じ MpTak_v7.1 アセンブリ（GCA_039105155.1）で代用できることを確認した。
- **作業記録を About の下へ移す**：処理サマリや戦略別ページは About の「Methods」「処理記録」に格下げし、研究（BioProject、PMID）と QC フラグを表に出す。

実装は既存の「静的サイト + S3」の構成のままで済み、Phase 0〜3 で合計 6.5〜8.5 人日を見込む（8 節）。

## 1. 現状の監査

### 1.1 調べた範囲

生成スクリプト（`scripts/build_catalog_pages.py`、`build_static_data.py`、`build_catalog_db.py`、`igv_url_helper.py`）、生成物（`report/` 以下と `report/assets/matrix.js`）、DB（`db/kknmsmd.db`）、公開中のサイトを読んだ。
公開サイトはヘッドレス Firefox を WebDriver BiDi で操作し、画面幅 390 px での表示、マトリクスから IGV への送信内容、コンソールのエラーを確かめた。
IGV への送信内容は、IGV の HTTP 応答をまねる偽サーバを 127.0.0.1:60151 に立てて受け取った。
S3 は `HEAD` で存在を確認した（バケットは一覧取得を許していないため、存在しないキーは 403 を返す）。

### 1.2 三つのフローを妨げているもの

以下では、フロー 1 を「探す」、フロー 2 を「選んでダウンロードする」、フロー 3 を「IGV で見る」と呼ぶ。

| # | 問題 | 根拠 | 妨げるフロー |
|---|---|---|---|
| A1 | トップが 2 軸マトリクスで、軸を二つ選ばないとデータが見えない。「bundle」「genotype_class」などの内部用語がそのまま出る | `render_index()`、`matrix.js` | 1 |
| A2 | ナビゲーション 9 項目のうち、Summary、Methods、戦略別 3 ページが同列に並ぶ。データの入口が Matrix と Browse の二つに割れている | `_nav()` | 1 |
| A3 | Browse の絞り込みは行全体の部分文字列一致だけで、条件を組み合わせられない（例：「H3」で H3K27me3 も H3K4me3 も H3 も当たる）。クイックチップも文字列を入れるだけ | `JS_FILTER_AND_CHIPS` | 1 |
| A4 | 戦略別ページには絞り込みがない | `JS_SORT_ONLY` | 1 |
| A5 | 研究単位の情報（BioProject、論文 PMID、GEO の GSM、サンプル名、装置）がない。研究者がまず探す「あの論文のデータ」に辿り着けない | DB スキーマ | 1, 2 |
| A6 | 表の列名が内部名のまま（`dev_stage`、`peaks_q5`、`cpg_hypermr_count`、`elapsed_min`）。処理時間や `status ok/failed` が主要列に並ぶ | `render_browse()`、`render_sample()` | 1 |
| A7 | キュレーションの表記揺れと誤り。遺伝子型は「WT」「wild type」「Tak-1」が別の値として並ぶ。PRJNA690531 の 6 実験では、抗体欄の値がサンプル名と食い違う（例：サンプル名 `H2AK119R_H3K27me3_R1` に対して抗体が `H2AK119R`）。遺伝子型の変異名を抗体として拾ったとみられる。結果として H3K27me3 で絞ると 3 実験が漏れる | DB の値とサンプル名の照合 | 1 |
| B1 | ダウンロードは実験ページのリンクを 1 ファイルずつ押すしかない。URL リスト、スクリプト、メタデータ表がない | `render_sample()` | 2 |
| B2 | ファイルサイズ、チェックサムの表示がない。S3 の ETag はマルチパートアップロードのため MD5 にならない（例：`"…-16"`） | `HEAD` の応答 | 2 |
| B3 | ピークの閾値表示が誤っている。Methods は「q-value thresholds 0.05, 0.10, 0.20」と書くが、実際は MACS3 の `-q 1e-05` で呼び、ファイル名の 05/10/20 は q < 1e-5、1e-10、1e-20 を表す（パイプラインの `-q 1e-05` と narrowPeak の 9 列目の最小値 5.03 で確認）。「peaks_q5」という表記も q < 0.05 と読まれやすい | `render_methods()`、`pipeline-v2.sh` 157 行目 | 2, 3 |
| B4 | S3 にある bigBed（`.05/.10/.20.bb`）と QC ファイル（`_fastp.json`、`.stats.tsv`）がどこからもリンクされていない | `render_sample()`、S3 の `HEAD` | 2, 3 |
| C1 | マトリクスの IGV 送信が壊れている。`dataUrl()` がベース URL 末尾の `/` を削ってから区切りなしで連結するため、`…amazonaws.comoutput/…` になる | `matrix.js` 18〜21 行目、偽 IGV での受信内容 | 3 |
| C2 | IGV への引数が `file=url|name,…` 形式だが、IGV はこの書式を解釈しない。`|name` を含めた全体を URL として取りに行く | IGV の CommandListener/CommandExecutor のソース [1][2] | 3 |
| C3 | `genome=` を送らないので、トラックは IGV が開いているゲノム（既定は hg38）に載る | `matrix.js`、`bundle.html` | 3 |
| C4 | ゲノムの FASTA、`.fai`、GFF が S3 にない（`chrom.sizes` のみ）。MarpolBase の配布物は通常の gzip（bgzip ではない）で `.fai` がなく、CORS ヘッダもないため、IGV からも igv.js からも直接は使えない | S3 と marchantia.info への `HEAD` | 3 |
| C5 | 失敗しても代替手段が出ない。`fetch` が成功扱いになれば「Sent」と表示し、失敗すればボタン文言が変わるだけ。`bundle.html` は `alert()` を出す。セッション XML の取得手段がない | `matrix.js`、`render_bundle_shell()` | 3 |
| C6 | 実験詳細ページに IGV ボタンがない。マトリクスで実験種別を軸に選ばないとトラックが作れない | `render_sample()`、`resolveStrategy()` | 3 |
| D1 | QC の目印がない。マッピング率は数値で出るだけで、ChIP-seq 89 件のうち 39 件が 50% 未満、11 件が 20% 未満という状況が利用者に伝わらない | DB の値 | 1, 2 |
| E1 | スマートフォン幅（390 px）で Browse の表がはみ出す。文書の幅は 893 px になり、横スクロールが必要 | 公開サイトの計測 | 1 |
| E2 | `enrichment.html` で `matrix.js` が読み込まれ、`TypeError: xsel is null` がコンソールに出る | 公開サイトのコンソール | なし（品質） |
| E3 | About の連絡先が「to be added」のまま。処理済みデータの利用条件、カタログの引用方法、リリース番号がない | `render_about()` | 2 |

### 1.3 先に直せる不具合

次の 4 点は、新 UI を待たずに現行サイトで直せる。
どれも数行の変更で済む。

1. `matrix.js` の URL 連結（C1）を `${DATA_BASE}/${path}` にする。
2. IGV への引数を `file=u1,u2&name=n1,n2&format=bigwig,bigbed&genome=<ゲノム JSON>` の形にする（C2、C3）。ゲノム JSON を置くまでは、`genome=` に UCSC GenArk のハブ URL を入れる（6.3 節）。
3. Methods と実験ページの閾値表記を「q < 1e-5 / 1e-10 / 1e-20」に直す（B3）。
4. `enrichment.html` と `bundle.html` から `init()` が走らないようにする（E2）。

## 2. 利用者像と主要フロー

### 2.1 想定する利用者

- **苔類の分子生物学者**：自分の遺伝子の周辺で H3K27me3 や ATAC のシグナルを見たい。コマンドラインは最小限にしたい。IGV デスクトップは使える。
- **計算系の研究者**：ある標的の全ピークとメタデータを一括で取り、比較解析に使いたい。スクリプトでの取得と、再現のための版管理を求める。
- **論文の読者や査読者**：論文や他のデータベースから特定の実験に直接来る。元の研究、処理条件、QC を確かめ、引用方法を知りたい。

### 2.2 フロー 1：探す

1. トップ（データ検索）を開くと、157 実験が表で見えている。上部に件数（実験 157、研究 11、ChIP/ATAC/BS 90/36/31）とゲノム版が出る。
2. 検索欄に「H3K27me3 bmi1」と打つか、左のファセットで「ChIP target: H3K27me3」「Genotype class: mutant」を選ぶ。ファセットの件数は他の条件を反映して変わる。
3. 研究で探す場合は「Study」ファセットか研究一覧から入る。研究は「Liu et al. 2021」のように筆頭著者と年で表示し、題名は補足に回す。
4. 条件は URL に入るので、検索結果をそのまま共有できる（例：`#/?assay=ChIP-seq&target=H3K27me3`）。

現行では、Browse の文字列フィルタで「H3K27me3」と打ち、遺伝子型は目で拾うしかない。
研究や論文からは探せない。

### 2.3 フロー 2：選んでダウンロードする

1. 検索結果で全選択（見出しのチェック）し、「Add selected to cart」を押す。1 件ずつなら行の「+ Cart」。
2. 画面下にカートの帯が出て、件数、ファイル数、合計サイズが常に見える。
3. カートで、含めるファイル種別（シグナル bigWig、ピーク bigBed、ピーク narrowPeak、メチル化レベル、被覆、領域 BED、QC レポート）、ピーク閾値、ローカルのファイル名の付け方を選ぶ。
4. 出力を 5 形式から選び、「Download file」か「Copy」を押す。QC が FAIL の実験が含まれていれば警告し、一括で外すボタンを出す。
5. 選択内容は「Copy share link」で URL にできる（`#/cart?add=SRX…,DRX…`）。

現行では、実験ページを開いてリンクを 1 本ずつ押す。
H3K27me3 の 10 実験でシグナルとピークを取るには、実験ページの往復を含めて 30 回以上のクリックが要る。
提案では、件数によらず 6 回前後（標的のチップ、全選択、カートへ追加、ダウンロードへ、形式の選択、保存）で済む。

### 2.4 フロー 3：IGV で見る

1. 行の「IGV」、実験詳細の「Open in IGV」、カートの「Open in IGV」のどれかを押す。
2. ダイアログで開始座位とゲノムの読み込み方を確かめ、「Send to IGV」を押す。初回は Chrome と Edge（Firefox も順次）が「このデバイス上の他のアプリへのアクセス」を尋ねるので、許可する。
3. IGV が応答すれば「Loaded in IGV」と出る。届かなければ、理由と代替手段（通常リンクで送る、セッション XML を落とす、ブラウザ内プレビュー）が強調表示される。

現行では、マトリクスの軸に実験種別を選んだうえで送信するしかなく、送られる URL も壊れている。

## 3. 推奨する設計

### 3.1 方針

ファセット検索、結果表、カートの 3 点で組む。
157 実験なら索引全体（gzip 後 28 KB）をブラウザに読み込み、絞り込みもマニフェスト生成もブラウザ内で済ませられる。
件数が 1 桁増えても同じ方式が使える（索引は 1,000 実験で約 2 MB と見込む）。

他の案も検討したが、採らなかった。

- **マトリクスをトップに残す案**：全体像を一目で見せるのには向くが、軸の意味を理解するまで何も選べない。Tools の「概観マトリクス」として残す。
- **ChIP-Atlas の Peak Browser 型（アッセイ、抗原の分類、細胞種、閾値を順に選ぶフォーム）**：選択肢が 1 列ずつ絞られるので迷いにくいが、複数実験の選択や一括ダウンロードに向かない。
- **フレームワークを使う SPA（React など）**：ビルド工程が増える。素の JS で足りる規模である。

### 3.2 他のポータルから取り入れるもの

| ポータル | 取り入れるもの | 取り入れないもの |
|---|---|---|
| ChIP-Atlas | `localhost:60151/load?…&genome=<ゲノム JSON>` による IGV 連携。ゲノム JSON の `id` を JSON 自身の URL にする工夫 [3][4]。閾値を 05/10/20 で揃える慣行 | 入力フォーム型の選択 |
| ENCODE | ファセット検索、カート、`files.txt`（1 行 1 URL）と `metadata.tsv` による一括取得 [5] | 大規模向けの複雑なファセット、ログイン |
| Cistrome DB | QC 指標を色付きの印で並べる表示 [6] | 6 指標の固定。zenigoke は QC 調査の結果に合わせて指標を差し替えられる形にする |
| ChIP-Hub | 植物向けの再解析ポータルとして、ENCODE 由来の QC 指標（FRiP、NSC/RSC、NRF、PBC）を示し、メタデータとピークを Zenodo に預けている [7] | WashU や JBrowse の埋め込み |
| PCSD | 種ごとのクロマチン状態を UCSC Genome Browser で見せる [8] | 状態解析そのもの |

ChIP-Hub は 40 種以上の植物を扱うが、論文中に Marchantia は挙がっていない [7]。
ゼニゴケに絞ったカタログとしての zenigoke の位置づけは、ここで説明できる。

### 3.3 サイトマップ

```
Data（トップ：検索 + ファセット + 結果表）       index.html
├─ 実験詳細                                       samples/{ACC}.html（URL は現行のまま）
├─ Cart（ダウンロードと IGV）                     cart.html
Studies（研究 = BioProject 一覧）                 studies.html
Genome & IGV（ゲノムファイル、IGV の準備、接続テスト） genome.html
Tools                                              tools.html
├─ Enrichment analysis                            enrichment.html（現行）
└─ 概観マトリクス                                 matrix.html（現行の index.html を移設）
About                                              about.html
├─ 引用とデータ利用条件、連絡先、リリース履歴
├─ Methods                                        methods.html（閾値表記を修正）
└─ 処理記録（メンテナ向け）                       processing.html（現行の summary.html）
```

現行ページの扱いは次のとおりである。

| 現行 | 扱い |
|---|---|
| `index.html`（マトリクス） | Tools の下へ移す。トップは Data にする |
| `browse.html` | Data に統合し、旧 URL は Data へ転送する |
| `strategy/{chipseq,atacseq,bsseq}.html` | Data の `#/?assay=ChIP-seq` などへ転送する |
| `samples/{ACC}.html` | URL を維持し、中身を作り直す（論文や外部からのリンクを切らないため） |
| `bundle.html` | カートに置き換える。旧 URL の `acc=` は `cart.html#/?add=` へ転送する |
| `summary.html` | About の下の「処理記録」へ格下げする |
| `methods.html`、`about.html`、`enrichment.html` | 位置を変え、内容を補う |

モックアップは 1 枚の HTML とハッシュによる画面切り替えで作ったが、本番では実験詳細を静的ページとして生成することを勧める。
検索エンジンや論文からの恒久リンクが効き、JavaScript なしでも基本情報が読めるからである。

### 3.4 ワイヤーフレーム

実物は `mockup/` と、モックアップを操作したときのスクリーンショットを参照のこと。

- [データ検索（H3K27me3 と bmi1 で絞った状態）](img/01-search.png)
- [実験詳細](img/02-sample.png)
- [カートとダウンロード](img/03-cart.png)
- [IGV に届かなかったときのダイアログ](img/04-igv-fallback.png)
- [igv.js で S3 の実データを GenArk のゲノムに重ねた表示](img/05-igvjs-genark-preview.png)
- [スマートフォン幅（390 px）](img/06-mobile.png)
- [Genome & IGV（ゲノムファイルの設置状況）](img/07-genome-igv.png)

データ検索（トップ）：

```
┌───────────────────────────────────────────────────────────────────────────┐
│ [Mp] zenigoke   Data  Studies  Genome&IGV  Tools  About        (Cart 3)  │
├───────────────────────────────────────────────────────────────────────────┤
│ Marchantia polymorpha epigenome data, uniformly reprocessed               │
│ 157 experiments | 11 studies | 90/36/31 ChIP/ATAC/BS | MpTak v7.1         │
│ [Q  H3K27me3 bmi1                         ] (ChIP-seq)(ATAC-seq)(H3K27me3)… │
├──────────────┬────────────────────────────────────────────────────────────┤
│ Filters  Clear│ 2 of 157 experiments  ("bmi1"x)(H3K27me3x)     Sort [v]   │
│ ▾ Assay       │ [Add 2 selected to cart] [Add all] [Open selected in IGV]  │
│  [x] ChIP  90 │ ┌─┬───────────┬──────────┬─────────┬──────┬──────┬──┬─────┐ │
│  [ ] ATAC  36 │ │□│Experiment │Assay/tgt │Genotype │Stage │Study │QC│Peaks│ │
│ ▾ ChIP target │ ├─┼───────────┼──────────┼─────────┼──────┼──────┼──┼─────┤ │
│  [find…    ]  │ │■│SRX9800305 │ChIP      │Mpbmi1/1l│thallu│Liu   │W │9,917│ │
│  [x] H3K27me3 │ │ │ R2_L16    │H3K27me3  │mutant   │      │2021  │  │[IGV]│ │
│ ▾ Genotype cl.│ └─┴───────────┴──────────┴─────────┴──────┴──────┴──┴─────┘ │
│ ▾ Study       │ QC flags are provisional (rules: …)                        │
│ ▾ QC status   │                                                            │
├──────────────┴────────────────────────────────────────────────────────────┤
│ 3 experiments in cart · 11 files · 403 MB        [Open in IGV] [Download…] │
└───────────────────────────────────────────────────────────────────────────┘
```

実験詳細：

```
Data › ChIP-seq › H3K27me3 › SRX9800305
┌───────────────────────────────────────────────────────────────────────────┐
│ SRX9800305 (WARN)                [+Add to cart][Open in IGV][Preview][XML]│
│ ChIP-seq of H3K27me3 in Mpbmi1/1l, thallus                                │
│ ! QC WARN: mapping rate 22.6% < 50%; duplication 77.4% > 60%              │
├────────────────────┬──────────────────────┬───────────────────────────────┤
│ Sample             │ Study                │ Sequencing & processing       │
│ target, genotype,  │ title, PRJNA690531,  │ SRR, instrument, reads,       │
│ stage, tissue,     │ PMID 34465381,       │ mapping, duplication,         │
│ BioSample, GSM     │ 24 experiments →     │ genome, pipeline version      │
├────────────────────┴──────────────────────┴───────────────────────────────┤
│ Results: peaks q<1e-5 / 1e-10 / 1e-20 (or mCpG/mCHG/mCHH, HMR, PMD)       │
│ Files: content | format | size | [Download] [Copy URL]   [Copy all][.sh]  │
│ How to cite / Related experiments (same study, same target elsewhere)     │
└───────────────────────────────────────────────────────────────────────────┘
```

カートとダウンロード：

```
┌──────────────────────────────────┬────────────────────────────────────────┐
│ 5 experiments  [Remove 3 FAIL]   │ Download                               │
│ [Copy share link] [Empty cart]   │ Files: [x] signal bigWig      3 files  │
│ ! 3 flagged QC FAIL              │        [x] peaks bigBed       3 files  │
│ SRX9800304 ChIP H3K27me3 FAIL [x]│        [ ] peaks narrowPeak   3 files  │
│ SRX9800305 ChIP H3K27me3 WARN [x]│        [x] methylation bw     3 files  │
│ DRX162963  BS             FAIL[x]│ Peak threshold: ( )1e-5 (•)1e-10 ( )1e-20│
│ …                                │ Names: (•) original ( ) descriptive    │
│ [Open in IGV] [Preview] 10 tracks│ 13 files · 512 MB                      │
│                                  │ [URL list][Bash][Metadata][Files][XML] │
│                                  │ ┌────────────────────────────────────┐ │
│                                  │ │ https://…/SRX9800305.bw            │ │
│                                  │ └────────────────────────────────────┘ │
│                                  │ [Download file] [Copy]                 │
└──────────────────────────────────┴────────────────────────────────────────┘
```

IGV への受け渡し：

```
┌ Open in IGV desktop ─────────────────────────────── [x] ┐
│ 10 tracks from cart (5 experiments).                     │
│ Start at locus [chr6:2440000-2490000      ]              │
│ Genome [MpTak v7.1 + MarpolBase genes (zenigoke, S3) v]  │
│ [Send to IGV]  Loaded in IGV. Switch to the IGV window.  │
│ ─ Other ways ─────────────────────────────────────────── │
│ [Download IGV session (.xml)] [Preview in browser]       │
│ [Send via link] [Copy IGV link]                          │
│ ▸ IGV isn't responding?（起動、ポート、許可、Safari）    │
└──────────────────────────────────────────────────────────┘
```

スマートフォンでは、ファセットを「Filters」ボタンの裏に畳み、結果表を 1 行 1 カードに組み替える。
モックアップでは 390 px 幅で文書の幅が 378 px に収まることを確かめた（`img/06-mobile.png`）。

### 3.5 用語と表記

- 単位は「実験（experiment、SRX/DRX）」に統一する。「sample」「bundle」は使わない。
- アッセイ名は ChIP-seq、ATAC-seq、BS-seq と書く。
- ピーク閾値は「q < 1e-5」「q < 1e-10」「q < 1e-20」と書き、既定は q < 1e-10 にする（現行マトリクスの既定と同じ）。
- 「status ok」「elapsed_min」は詳細の処理欄だけに置き、一覧には出さない。失敗した 1 実験は QC FAIL として扱う。
- 列名は人の言葉にする（例：`dev_stage` は Developmental stage、`peaks_q10` は Peaks q < 1e-10）。

## 4. ファイル命名とマニフェスト

### 4.1 S3 のキーは変えない

現行のキー `output/{chipseq|atacseq|bsseq}/{ACC}/{ACC}.<suffix>` は維持する。
IGV セッションや論文が URL を直接参照し始めると、改名でリンクが切れるからである。
人が読める名前は、表示ラベルと、ダウンロードスクリプトでのローカル名で与える。

| 接尾辞 | 表示名 | 形式 | IGV の既定 |
|---|---|---|---|
| `.bw` | Signal coverage | bigWig | 表示 |
| `.05.bb` `.10.bb` `.20.bb` | Peaks q < 1e-5 / 1e-10 / 1e-20 | bigBed | 選んだ閾値を表示 |
| `.05_peaks.narrowPeak` ほか | 同上（解析用テキスト） | narrowPeak | なし |
| `.{CpG,CHG,CHH}.methyl.bw` | Methylation level | bigWig | 表示 |
| `.{CpG,CHG,CHH}.cover.bw` | Read coverage | bigWig | なし |
| `.CpG.hmr.bed` | CpG HMR | BED | 表示 |
| `.CpG.hypermr.bed` `.CpG.pmd.bed` `.CHG.hypermr.bed` | hyperMR / PMD | BED | なし |
| `_fastp.json` `.stats.tsv` `.abismal.stats` | QC reports | JSON/TSV/YAML | なし |

`.05_peaks.xls` は MACS3 の中間出力なので公開一覧から外す。
ピークが 1 本も呼ばれなかった 3 実験（SRX29617447、SRX29617448、SRX29617451）にはピークファイルがないので、一覧にも出さない（索引はファイルの実在を確かめて作る）。

ローカル名は二通りを選べるようにした。

- **original**：`ChIP-seq/SRX9800305/SRX9800305.bw`
- **descriptive**：`ChIP-seq/SRX9800305/SRX9800305_ChIP-seq_H3K27me3_Mpbmi1-1l_thallus.bw`

### 4.2 一括ダウンロードの形式

| 形式 | 中身 | 使い方 |
|---|---|---|
| `files.txt` | 1 行 1 URL（ENCODE と同じ） | `xargs -n 1 -P 4 curl -fLO < files.txt`、`wget -i files.txt` |
| `download.sh` | `curl` だけを使う bash。アッセイ/実験ごとのディレクトリに保存。既知のサイズと一致するファイルは飛ばし、途中のファイルは `-C -` で続きから取る | `bash download.sh [dir]` |
| `metadata.tsv` | 1 行 1 実験。キュレーション値、研究と PMID、QC 指標と QC フラグ、ゲノム、リリース | Excel、R の `read.delim` |
| `files.tsv` | 1 行 1 ファイル。種別、形式、閾値、サイズ、ローカルパス、URL | `metadata.tsv` と `accession` で結合 |
| `igv_session.xml` | `<Session genome=… locus=…>` と `<Resource name path color>` | IGV の File › Open Session |

`download.sh` は、実際に S3 から 6 ファイルを取得し、再実行で全件を飛ばすこと、途中で切ったファイルを続きから取り直して元と一致することを確かめた。
当初は `curl -f -C -` だけで書いたが、取得済みのファイルで S3 が 416 を返して失敗したため、サイズ照合を加えた。

`metadata.tsv` の列は次のとおりである。

```
accession assay target target_class genotype genotype_class developmental_stage tissue
cell_type treatment sample_title bioproject sra_study run geo_sample biosample pmid
study_title layout instrument reads_after_trimming mapping_rate_pct duplication_rate_pct
peaks_q1e-5 peaks_q1e-10 peaks_q1e-20 mean_mCpG mean_mCHG mean_mCHH qc_flag qc_reasons
genome catalog_release
```

### 4.3 後で足すもの

- **チェックサム**：`md5sums.txt` をリリースごとに作り、`files.tsv` に `md5` 列を足す。ETag は MD5 の代わりにならない（B2）。
- **リリース単位のスナップショット**：`releases/2026-10/` に `catalog.json`、`metadata.tsv`、`files.tsv`、`md5sums.txt` を置く。大きなデータは複製せず、索引だけを固定する。
- **事前に固めた束**：閾値ごとの全ピーク（bigBed で 20〜31 MB、narrowPeak で 83〜152 MB）を zip にして置く。ピークだけ欲しい利用者にはこれが最短である。
- **Zenodo への登録**：メタデータとピークを預けて DOI を取る（ChIP-Hub と同じやり方 [7]）。

## 5. QC の表示

### 5.1 データの形

QC の調査は別途進んでいるので、判定の中身ではなく、判定を受け取る枠を決める。

```
qc_flag     PASS | WARN | FAIL
qc_reasons  ["mapping rate 22.6% < 50%", "duplication 77.4% > 60%"]
qc_source   provisional | curated     （規則による暫定判定か、QC 調査の確定判定か）
qc_version  "2026-10"                  （判定規則の版）
```

QC 調査の結果は `qc_flags.tsv`（`accession`、`qc_flag`、`reasons`）として渡してもらい、ビルド時に暫定判定を上書きする。
モックアップでは `mockup/meta/qc_overrides.tsv` がこの役割を持つ（現在は見出し行のみ）。

### 5.2 表示する場所

- 結果表の QC 列に色付きの印を出し、カーソルを載せると理由が出る。
- QC ファセットで PASS だけに絞れる。
- 実験詳細では、WARN と FAIL の理由を枠で囲って先頭に出す。
- カートでは FAIL を含むと警告し、一括で外すボタンを出す。
- `metadata.tsv` に `qc_flag` と `qc_reasons` を入れる。

既定では FAIL も隠さない。
カタログとして何を処理したかを見せることと、利用者が自分の基準で選べることを優先する。

### 5.3 暫定の規則

モックアップでは、次の規則で判定した。
数値は叩き台であり、QC 調査の結論で置き換える前提である。

| 判定 | 条件 |
|---|---|
| FAIL | パイプラインが完了しなかった / マッピング率 < 20% / ChIP と ATAC（対照を除く）で q < 1e-5 のピークが 0 本 |
| WARN | マッピング率 < 50% / 重複率 > 60%（ChIP、ATAC） / ChIP（対照を除く）で q < 1e-10 のピークが 1,000 本未満 / ATAC で 10,000 本未満 / 標的が未キュレーション |

この規則では PASS 84、WARN 54、FAIL 19 になった（ChIP-seq は 31/44/15、ATAC-seq は 29/7/0、BS-seq は 24/3/4）。
ピーク 0 本の 3 実験は H2A と H2A.X の ChIP なので、狭いピークが立たないこと自体は想定内かもしれない。
規則を一律に当てると、こうした標的を不当に落とす可能性がある。
標的の種類ごとに規則を分けられる枠にしておくのは、そのためである。

## 6. IGV への受け渡し

### 6.1 制約

調べた結果を、設計に効くものに絞って挙げる（出典は末尾）。

- **IGV 側**：現行の安定版は 2.19.8 で、ポート 60151 は既定で有効である [9]。HTTP では `/load`、`/goto`、`/execute?command=…` を受け付ける。`/echo` という経路はない [1]。`/load` の `file`、`name`、`format` は同じ長さのカンマ区切りで、`url|name` 書式は解釈されない [2]。応答は読み込みが終わってから返り、成功は 204、失敗は 200 と「ERROR…」本文で、どちらも `Access-Control-Allow-Origin: *` が付く [1]。
- **ゲノム指定**：`genome=` は URL を受け付け、`.json` ならゲノム JSON、`hub.txt` なら UCSC のハブとして読む [10]。IGV は引数を読み込み済みゲノムの `id` と比べ、ゲノムを読み込むたびにセッションを初期化する [10]。このため、`id` が URL と違うと毎回ゲノムを読み直し、読み込み済みのトラックが消えると考えられる（ソースからの推定）。ChIP-Atlas はゲノム JSON の `id` に JSON 自身の URL を入れて、これを避けている [4]。
- **混在コンテンツ**：HTTPS のページから `http://127.0.0.1` への要求は、Chrome、Edge、Firefox では許される。Safari は許さない（WebKit の該当バグは未解決）[11][12]。
- **ローカルネットワークアクセス（LNA）**：Chrome 142（2025 年 10 月）から、公開サイトからループバックへの要求に許可プロンプトが出る。`fetch` は `no-cors` でも対象になる。Chrome 145 で権限名が `loopback-network` になった。拒否されると、接続拒否と同じ `TypeError` になる。最上位のページ遷移は対象外である [13][14]。Firefox も 151 から順次有効にしている [15]。
- **最上位のページ遷移**：`<a href="http://127.0.0.1:60151/load?…">` で開けば、混在コンテンツにも LNA にも当たらず、Safari でも届く。IGV が 204 を返すと遷移は取り消され、元のページが残る（ChIP-Atlas の方式）。ただし IGV が起動していないとエラーページに置き換わり、ページ側からは成否が分からない [3][16]。

### 6.2 採用する手順

```
[Send to IGV]
  │
  ├─ navigator.permissions.query({name:"loopback-network"})  （142〜144 は "local-network-access"）
  │    denied → 「サイト設定で許可し直す」案内 + 代替手段を強調
  │
  ├─ GET http://127.0.0.1:60151/execute?command=version     （即答する。許可プロンプト中は待つ）
  │    TypeError → 「起動していない / ポート無効 / Safari」案内 + 代替手段を強調
  │    CORS で読めない古い IGV → no-cors で再試行し、届けば「送信のみ」モード
  │
  └─ GET http://127.0.0.1:60151/load?genome=<URL>&file=u1,u2&name=n1,n2&format=bigwig,bigbed
           &locus=chr6:2440000-2490000&merge=true             （タイムアウトなし）
       204 → 「Loaded in IGV」 / 200 "ERROR…" → IGV のメッセージをそのまま表示
```

代替手段はダイアログの下段に常に置く。

1. **Send via link**：同じ URL を通常のリンクとして開く。Safari と、会社のポリシーで LNA が止められた環境向け。
2. **IGV セッション XML**：File › Open Session で開く。ポートを使わないので、どの環境でも動く。`/load` と違い、トラックの色も指定できる [20]。
3. **ブラウザ内プレビュー（igv.js）**：IGV を入れていない人向けの確認用。
4. **Copy IGV link**：URL を IGV の外で使う人向け。

トラックの既定は、ChIP/ATAC がシグナル bigWig と選んだ閾値のピーク bigBed、BS-seq が CpG/CHG/CHH のメチル化 bigWig と CpG HMR である。
ピークは narrowPeak ではなく bigBed を渡す。
bigBed は表示範囲だけを Range 要求で取れるが、narrowPeak は全体を落としてからでないと表示できないからである。
トラック名はカンマを含まない形（`SRX9800305 H3K27me3 Mpbmi1/1l thallus | signal`）にした。
60 トラックを超えると、遅くなる旨を表示する。

モックアップでは、偽の IGV サーバを相手に、成功（204）、IGV のエラー（200 と「ERROR…」）、未起動（接続拒否）の 3 通りを確かめた。
送られた `/load` は `file`、`name`、`format` が同数で、項目の区切りが生のカンマであることも確認した。

### 6.3 ゲノムの扱い

本命は、zenigoke が自分でゲノムを S3 に置くことである。
解析に使った FASTA と同じものなので、染色体名（chr1〜chr8、chrV、chrU）がトラックと一致し、MarpolBase の遺伝子 ID（Mp1g00070 など、18,264 遺伝子）で検索できる。
置くファイルとゲノム JSON の中身は 7.1 節に示す。

暫定策として、UCSC GenArk の GCA_039105155.1（MpTak_v7.1）が使える [17]。
GenArk の `chrom.sizes` は zenigoke の `chrom.sizes` と 10 本すべて長さが一致し、別名表で chr1〜chr8、chrU、chrV が GenBank 名（AP031342.1 など）に対応づけられている。
UCSC は 2bit ファイルを CORS 付きで配っている。
igv.js 3.8.9 で `genome: "GCA_039105155.1"` を指定し、S3 の実データ（ChIP の bigWig と bigBed、ATAC、BS のメチル化 bigWig と HMR BED）を重ねて、chr6 の指定位置に正しく描画されることを確かめた（`img/05-igvjs-genark-preview.png`）。
ただし、GenArk の遺伝子トラックは NCBI の注釈（MPTK1_6g01760 などの ID）で、MarpolBase の ID では検索できない。
IGV デスクトップに `genome=<hub.txt の URL>` を渡したときの別名の扱いは未確認である（6.5 節）。

### 6.4 ブラウザ内プレビュー

igv.js 3.8.9 を jsDelivr から必要になったときだけ読み込む（cdnjs にはない）[18]。
igv.js は配列の取得元（FASTA か 2bit）を必須とし、`chromSizesURL` だけでは起動しない（igv.js のソースで確認）。
このため、ゲノムを S3 に置くまでは GenArk を使い、ブラウザを作ってからトラックを足す順にした。
トラックの一部が読めなくても、ゲノムの表示までは進む。
S3 の CORS は `https://*.github.io` を許しているので、本番のドメインではこのまま動く。
モックアップを `localhost:8765` で開いた場合はトラックが CORS で止まる。

### 6.5 未検証事項

- 実物の IGV デスクトップ（2.19.8 と 3.0 ベータ）で、ゲノム JSON、セッション XML（`Resource` 要素の `color` 属性が効くかを含む）、GenArk ハブの読み込みを確かめていない。IGV のソースとドキュメントからの判断である。
- Chrome 142 以降の LNA プロンプトと、`navigator.permissions` の状態遷移を実機で見ていない（試験に使った Firefox 136 は LNA 導入前）。
- Safari で「Send via link」が通ること、Safari 18.2 の「HTTP を警告」設定で止まらないことを確かめていない。

Phase 1 の終わりに、macOS（Chrome、Safari）、Windows（Edge）、Linux（Firefox）と IGV 2.19.8 の組み合わせで、この 3 点を手で確かめる。

## 7. 必要なデータとビルドの変更

### 7.1 ゲノムを S3 に置く

ビルドマシン（ota-ws-01）に必要なファイルはそろっている。
`/data1/zenigoke/references/MpTak_v7.1/` に `MpTak_v7.1.fa`（252 MB）、`.fai`、`MpTak_v7.1.gff`（33 MB、GFF3）がある。

```bash
export AWS_PROFILE=chiba-dsc
REF=/data1/zenigoke/references/MpTak_v7.1
DST=s3://zenigoke-catalog-dsc/references/MpTak_v7.1
aws s3 cp $REF/MpTak_v7.1.fa     $DST/MpTak_v7.1.fa     --content-type text/plain
aws s3 cp $REF/MpTak_v7.1.fa.fai $DST/MpTak_v7.1.fa.fai --content-type text/plain
# GFF3 は拡張子が .gff3 でないと IGV が形式を判定しない。索引なしの gzip にすると遺伝子 ID 検索が効く（4.1 MB）
gzip -c $REF/MpTak_v7.1.gff > /tmp/MpTak_v7.1.genes.gff3.gz
aws s3 cp /tmp/MpTak_v7.1.genes.gff3.gz $DST/MpTak_v7.1.genes.gff3.gz --content-type application/gzip
aws s3 cp MpTak_v7.1.igv.json    $DST/MpTak_v7.1.igv.json --content-type application/json
```

`.gff3.gz` には `Content-Encoding: gzip` を付けない。
付けるとブラウザが途中で展開し、igv.js がもう一度展開しようとして失敗するおそれがある。

ゲノム JSON はモックアップの `make_index.py` が `mockup/data/MpTak_v7.1.igv.json` として書き出す。
要点は二つある。

```json
{
  "id": "https://zenigoke-catalog-dsc.s3.ap-northeast-1.amazonaws.com/references/MpTak_v7.1/MpTak_v7.1.igv.json",
  "name": "M. polymorpha MpTak v7.1",
  "fastaURL": ".../MpTak_v7.1.fa",
  "indexURL": ".../MpTak_v7.1.fa.fai",
  "chromosomeOrder": "chr1,chr2,chr3,chr4,chr5,chr6,chr7,chr8,chrV,chrU",
  "defaultPos": "chr6:2440000-2490000",
  "tracks": [{ "name": "MpTak v7.1 genes (MarpolBase)", "format": "gff3",
               "url": ".../MpTak_v7.1.genes.gff3.gz", "indexed": false, "searchable": true }]
}
```

- `id` を JSON 自身の URL にする（6.1 節）。
- 遺伝子モデルは索引なしの gzip にする。索引付き（tabix）にすると、igv.js では遺伝子名で検索できなくなる [19]。

あわせて、`deploy/aws/02-sync-data.sh` に `references/` の同期を足す。
現在は `chrom.sizes` も手で上げている（DEPLOYMENT.md の注意 5）。

MarpolBase はゲノムデータの利用時に論文の引用を求めている [21]。
再配布の可否は明記されていないので、公開前に MarpolBase へ一報入れることを勧める。
断られた場合は GenArk を本命に切り替え、MarpolBase の遺伝子モデルは配布元を案内する形にする。

### 7.2 カタログ索引 JSON

UI 全体が読む索引を 1 本作る（モックアップの `data/catalog.json` がその試作で、328 KB、gzip 後 28 KB）。

```
{
  schema, generated, release, data_base,
  genome:   { id, name, json, fasta, fai, gff, chrom_sizes, default_locus,
              genark: { accession, hub }, hosted: { json, fasta, fai, gff, chrom_sizes } },
  qc_rules: [ … ],
  studies:  { PRJNA690531: { title, label, srp, center, first_public, pmid, citation, n } },
  samples:  [ { acc, assay, target, target_class, genotype, genotype_class, stage, tissue,
                cell_type, treatment, title, study, srp, run, gsm, biosample, instrument,
                first_public, layout, status,
                qc: { flag, reasons, source },
                metrics: { reads, mapping_rate, duplication_rate, peaks{05,10,20}, meth{CpG,CHG,CHH,hmr,pmd} },
                files: [ { cat, q?, ctx?, key, fmt, label, igv?, size } ] } ]
}
```

GitHub Actions のビルドは `/data1` も `metadata/` も読めない（`metadata/` は Git の管理外）。
そこで、ファイルの実在とサイズ、研究情報、BioSample の題名は、ビルドマシンで DB に取り込む。
Actions は DB だけから索引と静的ページを作る。
現在 DB を S3 経由で Actions に渡している流れを、そのまま使える。

### 7.3 DB スキーマの追加

```sql
CREATE TABLE study (
  bioproject TEXT PRIMARY KEY, sra_study TEXT, title TEXT, center TEXT,
  first_public TEXT, pmid TEXT, citation TEXT
);
ALTER TABLE sample ADD COLUMN bioproject TEXT REFERENCES study(bioproject);
ALTER TABLE sample ADD COLUMN run_accession TEXT;
ALTER TABLE sample ADD COLUMN geo_sample TEXT;
ALTER TABLE sample ADD COLUMN sample_title TEXT;
ALTER TABLE sample ADD COLUMN instrument TEXT;
CREATE TABLE sample_qc (
  accession TEXT PRIMARY KEY REFERENCES sample(accession),
  qc_flag TEXT NOT NULL, qc_reasons TEXT, qc_source TEXT, qc_version TEXT
);
CREATE TABLE sample_file (
  accession TEXT REFERENCES sample(accession), category TEXT, threshold TEXT,
  s3_key TEXT PRIMARY KEY, format TEXT, size_bytes INTEGER, md5 TEXT
);
```

研究情報は ENA Portal API（`result=read_experiment` と `result=study`）と NCBI E-utilities（BioProject から PubMed への elink）で取れる。
157 実験すべてが 11 の BioProject に対応し、そのうち 8 件に PMID が付いた。
取得結果は TSV としてリポジトリに置き、DB 作成時に読む（モックアップの `meta/*.tsv` と同じ形）。

### 7.4 メタデータの正規化

検索の質は、ファセットに入る値の質で決まる。
公開前に、次の対応表を作って DB 作成時に当てることを勧める。

- **遺伝子型**：「WT」「wild type」を統一し、背景系統（Tak-1、Tak-2、Tak-1 BC）と、変異か過剰発現かの別を分けて持つ。現行の `genotype_class` の規則はそのまま使える。
- **抗体と標的**：PRJNA690531 の 6 実験は、抗体欄に遺伝子型の変異名が入っている（SRX9800286、287、295、300、302、303）。サンプル名から読める正しい値（H3、H2Aub、H3K27me3）に直す。H2Aub と H2AK119ub は同じ修飾の表記揺れの可能性があるので、キュレーターが確かめる。
- **抗体と標的を分ける**：タグ ChIP（RFP 融合 WRKY など）では、抗体は抗 RFP、標的は WRKY14 である。現状は反復ごとに `tagRFP` と `WRKY14` に割れている（SRX8604219 と相方）。`antibody` と `target` の 2 列にする。
- **未キュレーションの 3 実験**（SRX15503703、SRX15503704、SRX8604214）は、GEO の GSE 記述から手で埋める。

### 7.5 ビルドとデプロイ

- `scripts/build_catalog_pages.py` は、索引 JSON と静的な実験ページの生成に作り替える。マトリクス用 JSON の生成（`build_static_data.py`）は Tools の概観マトリクス用に残す。
- `pages.yml` の `paths` に新しいスクリプトと共通 JS/CSS を足す。
- JS と CSS はファイル名か問い合わせ文字列に版を入れ、ブラウザのキャッシュで古い版が残らないようにする（モックアップの試験中に、古い `app.js` が使われ続ける事象が実際に起きた）。
- S3 の CORS は現状のままでよい。独自ドメインに移す場合は、そのオリジンを足す。

## 8. 段階的な実装計画

工数は Claude Code と並走する開発者 1 名を想定した目安である。

| 段階 | 内容 | 工数 | 完了の判断 |
|---|---|---|---|
| Phase 0 | 現行サイトの不具合修正（1.3 節の 4 点）。IGV 引数は GenArk ハブで暫定対応 | 0.5 日 | マトリクスから送った URL が S3 で 200/206 を返し、IGV の偽サーバでの受信内容が正しい |
| Phase 1 | ゲノムを S3 に置く（7.1 節）。DB に研究、QC、ファイルの表を足す（7.3 節）。正規化表（7.4 節）。索引 JSON の生成 | 1.5〜2 日 | `genome=<JSON>` で IGV が MpTak v7.1 と遺伝子モデルを読み、Mp 遺伝子 ID で移動できる。6.5 節の手動確認を済ませる |
| Phase 2 | 新しい Data ページ（ファセット、結果表、カート、5 形式の出力）、実験詳細の作り直し、IGV 受け渡しと代替手段。旧 URL の転送 | 3〜4 日 | 2.2〜2.4 節の各フローがモックアップと同じ操作数で通る。node のテストと pytest が通る |
| Phase 3 | IA の整理（About、Methods、処理記録、Studies、Genome & IGV）、igv.js プレビュー、スマートフォン対応、アクセシビリティの点検 | 1.5〜2 日 | 390 px 幅で横スクロールが出ない。キーボードだけで検索からダウンロードまで操作できる |
| Phase 4 | md5 とリリースのスナップショット、閾値ごとのピーク束、Zenodo の DOI、schema.org の Dataset 記述、QC 調査結果の取り込み | 随時 | |

Phase 0 は単独で出せる。
Phase 1 と 2 は並べて進められるが、IGV の受け渡しを本番に出すのは Phase 1 のゲノム設置の後にする。

## 9. 決めてほしいこと

1. MarpolBase のゲノムを S3 で再配布してよいか（7.1 節）。可否によって、ゲノムの本命を自前ホストにするか GenArk にするかが決まる。
2. QC FAIL の実験を既定で表示するか。本書は表示する案である（5.2 節）。
3. 処理済みデータの利用条件（例：CC BY 4.0）と、カタログの引用先（Zenodo の DOI か論文か）。
4. 公開ドメインを github.io のままにするか、独自ドメインに移すか。移すなら S3 の CORS と IGV のリンクに影響する。

## 10. モックアップ

### 10.1 置き場所と起動

```
docs/proposals/2026-10-01-ui/
├─ proposal.md                 本書
├─ img/                        検証時のスクリーンショット
└─ mockup/
   ├─ index.html               画面の骨組み（ハッシュで画面を切り替える 1 枚の HTML）
   ├─ css/app.css
   ├─ js/core.js               絞り込み、マニフェスト、IGV の URL と XML の生成（DOM に触れない）
   ├─ js/app.js                画面
   ├─ data/catalog.json        make_index.py の出力
   ├─ data/MpTak_v7.1.igv.json S3 に置く予定のゲノム JSON
   ├─ meta/                    ENA/NCBI から取った研究情報の控え、QC 上書き表
   ├─ make_index.py            db/kknmsmd.db などから索引を作る（標準ライブラリのみ）
   └─ test/core.test.js        core.js のテスト（node だけで動く）
```

```bash
cd docs/proposals/2026-10-01-ui/mockup
python3 make_index.py                 # 索引を作り直す（--refresh-meta で ENA/NCBI から再取得）
python3 -m http.server 8765           # http://localhost:8765/ を開く
node test/core.test.js                # 11 件のテスト
```

### 10.2 確かめたこと

- `core.js` のテスト 11 件（ファセットの論理、件数、URL への状態の保存、ファイル選択、各マニフェストの形、IGV の URL とセッション XML、igv.js の設定）が通る。セッション XML は Python の XML パーサで、`download.sh` は `bash -n` で構文を確かめた。
- ヘッドレス Firefox で、検索、ファセット、全選択とカート投入、実験詳細、共有リンクからのカート復元、5 形式の出力、IGV ダイアログ（成功、IGV のエラー、未起動の 3 通り）、研究一覧、Genome & IGV、390 px 幅の表示を操作し、コンソールにアプリ由来のエラーが出ないことを確かめた。
- 生成した `download.sh` で S3 から実際に取得し、再実行と途中からの再開を確かめた。
- igv.js 3.8.9 と GenArk のゲノムで、S3 の実データが描画されることを確かめた（試験用の CORS 中継を使用）。

### 10.3 モックアップの限界

- 画面は 1 枚の HTML で、実験詳細も同じページ内で切り替える。本番では静的ページにする（3.3 節）。
- ゲノムがまだ S3 にないので、IGV ダイアログの既定は GenArk になっている。
- `localhost` で開いた場合、ブラウザ内プレビューのトラックは S3 の CORS で止まる。github.io に置けば動く。
- QC フラグは暫定の規則による。
- UI の文言は英語で書いた。公開サイトの利用者を海外の研究者まで含めて想定したためである。

## 出典

- [1] IGV CommandListener（main と v2.19.8）: https://github.com/igvteam/igv/blob/main/src/main/java/org/igv/batch/CommandListener.java , https://github.com/igvteam/igv/blob/v2.19.8/src/main/java/org/broad/igv/batch/CommandListener.java
- [2] IGV CommandExecutor: https://github.com/igvteam/igv/blob/main/src/main/java/org/igv/batch/CommandExecutor.java ; ResourceLocator: https://github.com/igvteam/igv/blob/main/src/main/java/org/igv/util/ResourceLocator.java
- [3] ChIP-Atlas の IGV リンク: https://github.com/inutano/chip-atlas/blob/master/views/experiment.haml , https://github.com/inutano/chip-atlas/blob/master/public/js/pj/peak_browser.js
- [4] ChIP-Atlas のゲノム JSON（`id` が自身の URL）: https://chip-atlas.dbcls.jp/data/genome/hg38/hg38.json
- [5] ENCODE Batch download: https://www.encodeproject.org/help/batch-download/
- [6] Zheng R. et al. Cistrome Data Browser, Nucleic Acids Res. 2019: https://academic.oup.com/nar/article/47/D1/D729/5193328
- [7] Fu L.-Y. et al. ChIP-Hub provides an integrative platform for exploring plant regulome, Nat. Commun. 2022: https://www.nature.com/articles/s41467-022-30770-1
- [8] Liu Y. et al. PCSD: a plant chromatin state database, Nucleic Acids Res. 2018: https://academic.oup.com/nar/article/46/D1/D1157/4429024
- [9] IGV ダウンロードと外部制御: https://igv.org/doc/desktop/DownloadPage/ , https://igv.org/doc/desktop/UserGuide/advanced/external_control/ , 既定設定 https://github.com/igvteam/igv/blob/v2.19.8/src/main/resources/org/broad/igv/prefs/preferences.tab
- [10] IGV のゲノム読み込み: https://github.com/igvteam/igv/blob/v2.19.8/src/main/java/org/broad/igv/feature/genome/GenomeManager.java , https://igv.org/doc/desktop/FileFormats/Genomes/
- [11] MDN browser-compat-data（mixed content）: https://github.com/mdn/browser-compat-data/blob/main/http/mixed-content.json
- [12] WebKit Bug 171934: https://bugs.webkit.org/show_bug.cgi?id=171934
- [13] Chrome Local Network Access: https://chromestatus.com/feature/5152728072060928 , https://developer.chrome.com/release-notes/142
- [14] LNA の説明文書（遷移の扱い、権限名）: https://docs.google.com/document/d/1QQkqehw8umtAgz5z0um7THx-aoU251p705FbIQjDuGs/edit , MDN https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Local_network_access
- [15] Firefox 151 リリースノート: https://www.firefox.com/en-US/firefox/151.0/releasenotes/
- [16] HTML 仕様（204 応答での遷移の扱い）: https://html.spec.whatwg.org/multipage/browsing-the-web.html
- [17] UCSC GenArk GCA_039105155.1: https://hgdownload.soe.ucsc.edu/hubs/GCA/039/105/155/GCA_039105155.1/
- [18] igv.js（npm と jsDelivr）: https://registry.npmjs.org/igv/latest , https://cdn.jsdelivr.net/npm/igv@3.8.9/dist/
- [19] igv.js の注釈トラック（`searchable`）と参照ゲノム: https://igv.org/doc/igvjs/tracks/Annotation-Track/ , https://igv.org/doc/igvjs/Reference-Genome/
- [20] IGV のセッション: https://igv.org/doc/desktop/UserGuide/sessions/
- [21] MarpolBase（引用の依頼はトップページ）: https://marchantia.info/ , MpTak v7.1: https://marchantia.info/data/v7.1_reference_genomes/MpTak_v7.1_standard_genome/
