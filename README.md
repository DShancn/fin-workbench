# fin-workbench

个人 A 股财报分析工作台。点任何一个数字，都能展开「它怎么算的、由哪些原值得出、阈值是谁定的、判定理由是什么、这个指标该怎么读、去年报哪一科核对」。

**一边看财报，一边学财报**——这是它和普通看数工具的区别所在。

它是 [vest](https://github.com/ds1828743190-oss/alibabashiyong) 题材归因管线的下游：归因卡筛出「值得深挖的标的」，这里负责深挖。

---

## 核心设计：判定权归代码，解释权归模型

财报分析最容易出的错，是让语言模型既算数又下结论——它会给出流畅、自信、错误的答案。

这个项目把两件事彻底分开：

```
数字与判定  ← 代码算，可复现，同一份数据永远得到同一个结论
  ratios.py    四个比率（公式取自 FinanceToolkit，MIT）
  fscore.py    Piotroski 九条，按论文规格实现
  pailei.py    规则引擎，阈值来自 rules/pailei.md

解释与联想  ← 可以交给 agent，但无权改判定
  rules/notes.md  每个指标「为什么重要、怎么读」
  导出卡片 JSON   交给 agent 做解读
```

界面里每个指标卡展示的是：**代码给出的裁决 + 知识层给的解读**，两者来源分列，不会混。

### 阈值分两种，且强制标注出处

`rules/pailei.md` 里的阈值是**你定的**；`rules/notes.md` 里的经验值是**教材说的**。冲突时以你的规则为准。

这不是靠提示词去约束模型「请以用户规则为准」——那种约束在长上下文里会失效。这是**数据结构层面的保证**：每条阈值自带 `kind: user_rule | textbook`，界面上必须显示来源，模型想覆盖也覆盖不掉。

目前的实际状态：`rules/pailei.md` 里的阈值全部是 `user_rule`，教材经验值以**文字注解**的形式出现在注释面板，尚未做成结构化的 `textbook` 阈值条目。真正起作用的护栏是 `Metric.decisive_threshold`——它只认 `user_rule`，所以**任何教材说法都拿不到判定权**。

---

## 快速开始

有三个入口，按你想要的形态挑：

| 入口 | 命令 | 得到什么 | 需要装什么 |
|---|---|---|---|
| 一键起界面 | `./start.sh` | 交互工作台，浏览器自动打开 | 只要 Python ≥ 3.11 |
| 导成 HTML 报告 | `fw-report --all` | 自包含静态页面，双击即开、可离线存档/发给别人 | 同上 |
| Docker | `docker compose up -d web` | 服务化，取数与界面是两个容器 | 只要 Docker |

### 0. 最省事：`./start.sh`

```bash
cp watchlist.example.csv watchlist.csv     # 编辑成你自己的池子
./start.sh                                  # 建 venv → 装依赖 → 空库则自动取数 → 开浏览器
./start.sh --fetch                          # 只取数，不起界面
./start.sh --report                         # 只导 HTML 到 data/reports/
```

它幂等：已有 `.venv` 就直接复用，界面已在跑就不重复启动。日志落在 `data/streamlit.log`。

### 1. 界面（Streamlit）

`http://localhost:8501`。想放进任意内置浏览器/WebView 里打开就用这个地址——它是个正常的本地 web 服务，不依赖 Electron。

### 2. 自包含 HTML（`fw-report`）

```bash
fw-report 600519        # 单家 → data/reports/600519.html
fw-report --all         # 全库 + index.html（按风险分排序）
```

产出的页面**零 JS、零 CDN、样式内联**，点开指标的注释用的是原生 `<details>`。断网、拷到 U 盘、发微信文件都能打开。唯一的外链是巨潮年报 PDF。

界面和 HTML 走的是同一份 `brief` 载荷，不是两套算法——改一处判定，两边同时变。

### 3. Docker

```bash
cp watchlist.example.csv watchlist.csv     # 编辑成你自己的池子
docker compose build
docker compose run --rm fetcher            # 取数，一家公司约 2 分钟
docker compose up -d web                   # 打开 http://localhost:8501
```

改 `rules/pailei.md` 的阈值不用重新 build——它是挂载进容器的。

⚠️ 这套 compose 文件在开发机上**没有实际 build 过**（那台机器没有容器运行时）。结构按官方写法来，但第一次 `docker compose build` 可能要修依赖，不要把它当成已验证的路径。

### 手动安装（不想用 start.sh 时）

需要 Python ≥ 3.11。

```bash
uv venv && uv pip install -e .        # 或 pip install -e .
python -m fw.fetch 600519 000530      # 取数（唯一写进程）
streamlit run app/main.py             # 看界面（只读，不联网）
```

跑测试：

```bash
PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py" -v
```

---

## 命令

| 命令 | 作用 |
|---|---|
| `python -m fw.fetch <代码...>` | 取数入库。可给多个代码 |
| `python -m fw.fetch --queue` | 按「自选池 + vest 待办队」取数 |
| `python -m fw.fetch --queue --force` | 已入库的也重取 |
| `python -m fw.fetch --queue --disclosure-only` | 只补巨潮公告与 PDF 索引，不重取财务数据（省去每家 25 秒） |
| `python -m fw.fetch --status` | 看库里已有哪些公司和年度 |
| `fw-brief <代码>` | 打印结构化分析简报（给 agent 读的入口） |
| `fw-report <代码>` | 导出单家公司的自包含 HTML |
| `fw-report --all` | 全库导出 + 生成按风险分排序的 index.html |

界面是只读的：**它不联网**。取数和看报告是两个进程，原因是 DuckDB 单写者（见下文架构）。

---

## Agent 分析层

本项目**不内嵌 LLM 调用**。没有 API key、没有模型绑定、没有额外的网络失败面。

做法是把「事实与约束」打包交出去，推理留给任意 agent：

```bash
fw-brief 600519 --markdown     # 人读的分析简报
fw-brief 600519                # 结构化 JSON，字段更全
```

简报除了数值，还随数据带两段元信息：`authority`（声明这些判定由代码算出）和 `constraints`（七条行为契约）。这不是文档，是**传给模型的约束**——少了它，模型很容易拿着 `verdict` 又去推翻它。

`skill/fin-workbench-brief/` 是配套技能，让 agent 被触发时自动遵守这些纪律：不推翻判定、引用阈值必带规则编号与出处、覆盖率低时禁止横向比较、拒绝仓位与目标价。装上它以后，你只要对 agent 说一句「600519 财务上怎么样」。

```bash
cp -R skill/fin-workbench-brief ~/.qoder/skills/
```

一个值得留意的现象：**风险分与 F-Score 打架往往最有信息量**。前者量当前质地，后者量同比动量，所以「在变好」和「现在好」会给出相反的数字。这不是数据矛盾，别挑一个信。

---

## 三个视图

| 视图 | 干什么 | 直达链接 |
|---|---|---|
| 标注卡 | 单公司逐指标展开：公式、原值、阈值出处、判定理由、怎么读、去哪核对，末尾是年报与风险公告的 PDF 直链 | `?page=标注卡&code=603533` |
| 筛选 | 全库矩阵，按 ROE / 有息负债率 / F-Score 过滤，表头点击排序 | `?page=筛选` |
| 同业对比 | `watchlist.csv` 的 `group` 列分组内横向比较，含组内均值与质地散点 | `?page=同业对比&group=白酒` |

筛选器默认**保留**缺数据的行并说明原因（银行没有有息负债率概念，「未知」不等于「不合格」）；要按缺失排除，勾「严格模式」。

---

## 改规则

`rules/pailei.md` 直接编辑，每条一个 `## R<编号> <名称>` 段：

```markdown
## R1.1 商誉占归母净资产比
- field: goodwill_ratio
- check: lte
- warn: 0.30
- fail: 0.50
- learn: 商誉是并购溢价在账面上的残留，本身不产生现金流……
- drill: 资产负债表 · 商誉 / 附注 · 商誉减值测试
```

`layer: 0` 的条目是一票否决层——命中 FAIL 时界面顶部直接标红，因为后续所有比率都建立在不可信的报表上。

**解析器对错误零容忍**：字段名写错、阈值不是数字、`check` 取值非法，都会直接抛错并指出行号。这条是刻意的——静默跳过一条规则，比报错危险得多，因为界面会显示「全部通过」而实际只跑了 N-1 条。

有一条测试专门守这个契约：`test_every_rule_field_is_computable` 检查规则文件引用的每个 `field` 都在派生表里存在，防止改了规则却引用一个算不出来的字段。

---

## 与归因管线的接缝

```
vest 研究卡.followup_queue  →  fin-workbench 输入队列
```

`data/watchlist.csv` 之外，工具会自动读取 vest 库里的研究卡，把 `followup_queue` 每一项的标的与**具体问题**并进队列：

```
  code name     source                  question
603533 掌阅科技  vest:AI短剧@2026-10-14  AI短剧/数字阅读业务的实际收入占比与毛利率，是否与当前估值匹配
603598 引力传媒  vest:AI短剧@2026-10-14  临时股东会决议内容，以及全域营销业务的现金流质量
```

Docker 里把 vest 的库只读挂进来即可（`docker-compose.yml` 有注释好的示例行）。

vest 找不到时返回空队列，**不报错**——两个项目独立演进是正常状态。

---

## 架构

```
src/fw/
├── config.py        时区锚定、DB 路径、境内域名代理绕过
├── sources.py       akshare 取数（重试 + 显式失败）
├── fields.py        东财列名 → canonical 名映射
├── statements.py    长表归一、筛年报、滚动均值、有息负债合并
├── store.py         DuckDB，读写两条路径
├── derive.py        派生指标 + 字段元数据（公式、单位、分组、参与原值）
├── ratios.py        比率（FinanceToolkit 移植，MIT）
├── fscore.py        Piotroski 九条
├── rules_loader.py  rules/pailei.md 解析，严格失败
├── pailei.py        规则引擎
├── scoring.py       分层加权风险分（按覆盖权重归一）
├── metric.py        指标契约 —— 全工具唯一数据结构
├── registry.py      装配标注卡
├── screen.py        卡片压成矩阵 —— 筛选与对比的共同底座
├── disclosures.py   巨潮公告索引与 PDF 直链
├── brief.py         分析简报：把卡片交给 agent（含行为约束）
├── queue.py         自选池 + vest 队列合并
└── fetch.py         取数 CLI
app/main.py          Streamlit 界面（标注卡 / 筛选 / 同业对比）
rules/               pailei.md（你的规则）、notes.md（知识注解）
skill/               Qoder 技能：让 agent 遵守「只解释不裁决」的纪律
```

### 为什么是两个服务

DuckDB 是单写者模型：一个文件同一时刻只允许一个写连接，且只读连接会持共享锁。而本项目的两种访问节奏完全不同——取数一家公司要 2 分钟发 60+ 个请求，看报告要求秒开。

所以：**界面只读、永不联网**；取数是独立进程，采用「复制到暂存 → 修改 → 原子替换」，把与界面读锁的冲突窗口压到一次 rename。

这不是为了显得专业。是单写者约束下的必然形状。

### 指标契约

筛选、对比、标注、Agent 四个功能都消费同一个对象，不各写一套：

```python
Metric(key, label, value, unit, formula, inputs,     # 数值与它的来历
       thresholds=[Threshold(value, kind, origin)],  # 阈值 + 是用户的还是教材的
       verdict, verdict_by, verdict_reason,          # 代码裁决
       learn, drill)                                 # 解读 + 年报核对位置
```

### 数据源

财务数据单一来源：akshare 的东财三表按报告期接口（`stock_{profit|balance|cash}_flow_sheet_by_report_em`）。选它的原因是一个意外发现：**审计意见直接嵌在报表列里**（`OPINION_TYPE`），不需要额外接口——原本以为在 akshare 侧缺这一块。

公告溯源直连巨潮（`disclosures.py`），因为 akshare 的同类封装只给公告**详情页**链接、不给 PDF 直链，且把时间戳压成日期丢了精度。本项目自己解析 `adjunctUrl` 拼出 `static.cninfo.com.cn/...` 的 PDF 直链，并保留 `ts_precision`。orgId 一律走 `topSearch` 解析——按代码前缀猜会对 603xxx 之类的号段**静默返回空**。

注意成本：东财三表内部按年分页，单家公司一次取数约 25 秒、60+ 个子请求；公告索引再加约 30 次请求。所以**必须落库、增量更新**，界面只读。

注意成本：单家公司一张表内部分页 21 次请求，所以**必须落库、增量更新**。

---

## 已知问题与限制

- **境内数据源与本地代理冲突。** macOS 上 `urllib.request.getproxies()` 会读系统代理，把东财/巨潮的请求绕到科学上网通道，表现为随机 `SSLEOFError`、`read timeout`、以及 JSON 解析错误（`Expecting value: line 1 column 1`）。代码里已对境内域名强制绕过（`config.py`），Dockerfile 也兜了一层。**看到这个报错先怀疑代理，不要怀疑数据源。**
- **只读 A 股。** 交易时段、报告期口径、会计准则都是中国式的。
- **金融业报表结构与一般企业不同。** 银行/券商没有 `TOTAL_OPERATE_INCOME`（收入科目叫 `OPERATE_INCOME`），也没有营业成本与流动资产负债概念。字段映射带回退链所以营收能取到，但**毛利率、周转率、流动比率对金融业留 N/A 是正确行为**，不是数据缺失。对比页在整组皆为金融业时给出解释性提示而不是空白图。
- **同业分组靠你自己声明。** 东财 `push2` 系行业接口从开发机不可达（根路径可连通但 `/api/qt/clist/get` 一律 RST），同花顺行业接口已加反爬 401。因此 `watchlist.csv` 用 `group` 列显式分组——「谁和谁可比」本来就是判断而不是数据，少一个随时会变的第三方分类也更可靠。
- **F-Score 第 7 条口径退化。** akshare 三表没有加权平均股本，用期末注册股本代替（`SHARE_CAPITAL`）。对「有没有增发」这个二值判定够用，但不能拿它反推每股指标。
- **公告时间精度有上限。** 巨潮对多数公告只给日级精度（北京时间当地零点），因此「这条公告在股价异动前还是后」经常无法判定。工具会显式标出 `ts_precision=day` 而不是猜一个方向。
- **比值类指标的分母符号是持续的坑。** 分母为负时比值会翻正，「越大越好」的阈值反而判通过——实测已有公司归母 -1.76 亿、扣非 -1.94 亿，比值 1.101 被判成利润成色优秀。现在派生层强制：分母非正则该比值记 N/A，交由「净利是否为正」这类前提规则裁决（`rules/pailei.md` R0.3）。**新增任何比值指标都要守这条。**
- **Docker 链路未经真实构建验证。** 开发机没有容器运行时（docker/podman/colima/orbstack 均不可用），`Dockerfile` 与 `docker-compose.yml` 只做过人工核对——例如 Debian slim 缺 `tzdata` 会让时区锚定静默退化，这一点已写进镜像，但**整条构建没有实际跑通过**。首次 `docker compose build` 请当作可能需修来处理。

界面本身已在真实浏览器中逐视图验证过：标注卡、筛选、同业对比三个视图渲染成功，注释折叠面板内容、PDF 直链、以及三种空数据分支都确认无报错。

---

## 出处与许可

本项目按**可公开发布**的标准写，所以对第三方代码的引用做了严格区分：

| 来源 | 许可 | 本项目怎么用 |
|---|---|---|
| [FinanceToolkit](https://github.com/JerBouma/FinanceToolkit) | MIT | **移植了 4 个比率函数的公式**，`src/fw/ratios.py` 头部保留署名与源文件行号。不 pip install 它（要求 pandas≥3.0，与 akshare 生态冲突） |
| Piotroski (2000) 论文 | 公开学术成果 | **按算法规格自行实现** `src/fw/fscore.py`，未移植任何代码。同时修正了原论文的 4 处常见实现偏差 |
| 其余参考过的开源仓库 | **无 LICENSE** | 只读其逻辑与字段清单，**未复制任何文本**。规则文件与知识注解全部为本项目原创撰写 |

`src/fw/ratios.py` 若被再分发，须一并带上文件头的 MIT 版权声明。

本项目自有代码采用 MIT。

---

## 免责声明

个人研究工具，不构成投资建议。所有判定均为对公开财务数据的规则化整理，可能出错。据此做出的投资决策由使用者自行承担。
