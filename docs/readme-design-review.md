# README design review: 24 public sources

Inspection date: **2026-10-31 UTC**. This is an information-architecture review of **24 distinct public repositories**, in three groups of eight. The source notes below record the pages actually read by the research reviewers. They are moving-branch observations, not commit-pinned product verification. Projects were not installed or benchmarked. License names and maintenance notices are observed declarations at inspection time, not legal advice or a recommendation to adopt dependencies.

## Decisions for this repository

- Lead with a user outcome and audience, not the internal six-layer vocabulary.
- Offer one local, synthetic first-memory round trip with inspectable persistence and explicit correction history; distinguish engine evidence from host integration testing.
- Separate default-branch installation, published npm `0.1.0` (registry checked 2026-11-01), source version `0.1.1`, and source-only improvements. A repeated package version is not proof of feature availability.
- Put prerequisites, write approval, data paths, model-input privacy boundaries and project-ranking limits near the setup steps.
- Keep architecture and existing upstream acknowledgements, but put implementation depth after the first useful result.
- Keep evaluation limitations visible. Do not infer popularity causes, promise growth, reuse another project's numbers or imply that a synthetic test is real-user accuracy.

These are original editorial decisions, not copied prose, code, images or branding. All 24 sources below are references for abstract presentation patterns; their licenses do not automatically cover every linked asset. No third-party dependency is added. Suggestions in the matrices are **not claims that this repository already implements those features**. In particular, semantic search, automatic global lifecycle management, permission isolation and secure erasure are not promised.

The 24 canonical projects are distinct across groups. OpenMemory→LongMemory is counted once; corrected Memobase, QMD and Joplin repository names are not extra entries.


## Memory-project README comparison

Inspected on **2026-10-31 UTC**. This matrix covers **exactly eight distinct projects**. Actual GitHub-rendered or raw README bodies were opened; the observations are not based only on search snippets. The inspected branch was `main`. Commit identifiers and commit dates were not established, so these are dated observations of moving branches, not commit-pinned claims.

The purpose is to improve onboarding for a Chinese-first, local Markdown/BM25 memory system with a DeepSeekHarness plugin and approval-gated writes. Proposed wording and examples must be checked against that system's actual implementation.

| Project and source | Observed presentation pattern | Adaptation worth considering | License and copying caution |
| --- | --- | --- | --- |
| [mem0ai/mem0](https://github.com/mem0ai/mem0#readme) | An early benchmark table distinguishes managed-platform results from open-source SDK capabilities. A short initialize/add/search sequence accompanies a library/self-hosted/cloud comparison. | Provide one verified local round trip. Label benchmarks with dataset, command, environment and scope. Separate plugin and CLI paths. | GitHub declares Apache-2.0. Adopt the structure, not branding, benchmark numbers or automated signup behavior. |
| [letta-ai/letta-code](https://raw.githubusercontent.com/letta-ai/letta-code/main/README.md) | Interaction surfaces and a demo GIF precede a linked feature table. Setup makes supported platforms, troubleshooting and cloud/local defaults explicit. | State where memory lives, distinguish plugin from standalone CLI, and put setup-specific troubleshooting beside the relevant step. | GitHub declares Apache-2.0. Its self-rewriting memory behavior must not become an unsupported claim for an approval-gated system. |
| [basicmachines-co/basic-memory](https://raw.githubusercontent.com/basicmachines-co/basic-memory/main/README.md) | Explains shared human/agent Markdown, contrasts local/cloud storage responsibilities, and lists clients with transport/configuration notes. The current opening also contains substantial cloud promotion. | Lead with local file ownership, inspectability and backup responsibility. Use a small verified compatibility table; do not copy the cloud-heavy opening for a local-first tool. | Current README and GitHub declare AGPL-3.0. Do not assume an older permissive license. Use independent prose; implementation copying needs separate license review. |
| [getzep/graphiti](https://github.com/getzep/graphiti#readme) | Defines its central concept through a changing-fact example and a component table. It clearly separates framework responsibilities from managed-service responsibilities. | Explain memory items, source Markdown, index, retrieval results and approvals in a short table. State non-goals: BM25 is not semantic understanding; a plugin is not a complete agent. | GitHub declares Apache-2.0. Graph and temporal capability claims are not transferable to a BM25 implementation. |
| [topoteretes/cognee](https://raw.githubusercontent.com/topoteretes/cognee/main/README.md) | A task-to-entrypoint table precedes quickstart. Bundled demonstration and model-dependent setup are distinguished; first-use model downloads are disclosed. | Add a short path selector for sample inspection, plugin installation, CLI use and safety. Make the first demonstration deterministic and disclose network/model dependencies. | GitHub declares Apache-2.0. Independently create diagrams/examples; do not reuse demo media or imply identical no-key behavior without verification. |
| [supermemoryai/supermemory](https://raw.githubusercontent.com/supermemoryai/supermemory/main/README.md) | Early navigation exposes quickstart, self-hosting and Chinese translation. Onboarding separates AI-tool users, application developers and self-hosters. | Adopt audience routing and prominent Chinese navigation, limited to supported paths. Prioritize the host-plugin path over API internals for DeepSeekHarness users. | GitHub declares MIT. Do not transplant service claims, rankings, benchmark results or logos. Do not default to a remote-script installation pattern without a safety review. |
| [memodb-io/memobase](https://raw.githubusercontent.com/memodb-io/memobase/main/readme.md) | Concrete profile output is followed by numbered connection, insertion, processing, retrieval and prompt-integration stages. Processing is explained as necessary before derived memory appears. | Show candidate → approval pending → persisted Markdown → index → retrieval, with expected outputs. Explain why pending proposals are not yet searchable memories. | README declares Apache-2.0. Invent nonsensitive Chinese example data instead of copying its personal-profile sample. |
| [CaviraOSS/LongMemory](https://raw.githubusercontent.com/CaviraOSS/LongMemory/main/README.md) | A dashboard GIF and minimal ingest/recall example precede a separate SQLite persistence/reopening explanation. Library, CLI and server paths are separated. | Distinguish transient previews from durable storage; show the persisted file and retrieval after a new session. Keep server deployment out of the shortest local path. | GitHub declares Apache-2.0. Governed, temporal and multilingual capabilities belong to that project and are not automatically available in another system. |

### Identity and counting notes

- `https://github.com/memobase/memobase` returned 404. The inspected project is [memodb-io/memobase](https://github.com/memodb-io/memobase), not an additional project.
- [CaviraOSS/OpenMemory](https://github.com/CaviraOSS/OpenMemory) redirected to [CaviraOSS/LongMemory](https://github.com/CaviraOSS/LongMemory). They count as one project here. Other products named OpenMemory are not included.
- This was read-only documentation research. No project was installed or executed, and these observations do not verify advertised runtime behavior.
- License names are observed repository declarations, not blanket permission for all linked assets. Prefer original prose and diagrams; inspect applicable notices before copying code or assets.
- No causal inference is made between README design and popularity.

### Recommended onboarding sequence

1. One precise sentence stating audience, local Markdown storage, BM25 retrieval, host integration and approval-gated writes, after implementation verification.
2. Three concrete user benefits, without unmeasured speed or accuracy claims.
3. A tiny Chinese example showing that a proposal becomes persistent memory only after approval.
4. A path selector: DeepSeekHarness plugin, standalone CLI, or inspect a sample.
5. One tested happy path with prerequisites, commands, expected results and resulting file location.
6. Safety boundaries: reading versus writing, approval, secrets, storage, deletion and backup.
7. One original workflow diagram, followed by limitations, troubleshooting and deeper documentation.

The strongest transferable lesson is to make the first successful, verifiable retrieval easy to understand while making persistence and approval boundaries impossible to miss.

## 开发者工具 README 对照：首次有用结果

查阅日期：2026-10-31。实际只读查阅以下 8 个 GitHub 仓库的 README 正文；均可访问，未替换。观察范围为页面的信息组织，不包含安装测试或产品效果评估。下列建议面向小型中文 DSH 记忆插件：本地 Markdown、Python 标准库与 Node 接入。

### 证据矩阵

| 来源 | 实读观察 | 可迁移到 dsh-memory-system 的做法 | 许可与复制边界 |
|---|---|---|---|
| [LangGraph README](https://github.com/langchain-ai/langgraph#readme) | 开头界定低层、状态化编排定位，紧接安装入口；后续按持久执行、人工检查、记忆、调试列收益，另链高层入口。 | 首屏明确“DSH 的本地 Markdown 记忆插件”，列可检查的能力，不将小工具包装成完整智能体平台。 | 仓库页面标示 MIT。借鉴组织方式，原创表述，不移植品牌、客户背书或代码。 |
| [LangChain README](https://github.com/langchain-ai/langchain#readme) | Quickstart 较早出现，安装之后紧接最短调用；复杂编排及其他语言入口在后。 | 只设一条主要首用路径：写入一条记忆并检索；配置、Node 接入与扩展作为次级入口。 | 仓库页面标示 MIT。不复制示例正文；演示命令必须取自本项目真实接口。 |
| [LlamaIndex README](https://github.com/run-llama/llama_index#readme) | 所读开篇解释产品重心与框架定位；区分 starter/core；示例依次展示读取、查询、落盘、重载，并说明默认存储行为。 | 把“保存在哪里、重启后如何读取、何时更新索引”纳入首用闭环；区分必需环境与可选集成。 | 仓库页面标示 MIT。仅借“输入→查询→持久化→重载”的讲解顺序。 |
| [AutoGen README](https://github.com/microsoft/autogen#readme) | 所读版本在安装之前显示归档及维护警告和迁移入口；随后给版本要求、密钥前提及 Hello World。此处记录页面提示，不独立核验项目生命周期。 | 安装前说明兼容范围、实验性和已知限制；区分本地记忆功能与外部模型密钥需求。 | 页面列 CC-BY-4.0 与 MIT；[LICENSE](https://github.com/microsoft/autogen/blob/main/LICENSE) 为 CC-BY-4.0，另有 LICENSE-CODE。不可把文档默认当成 MIT；本次不复制内容。 |
| [CrewAI README](https://github.com/crewAIInc/crewAI#readme) | 区分两类工作流概念；安装含版本检查、验证命令与输出形态；项目生成后展示目录树；独立说明遥测和关闭方式。 | 给安装/初始化成功判据、生成文件树；加入简短数据流与遥测事实说明。 | README 明示 MIT。不移植其生产级、性能或社区规模宣称。 |
| [OpenAI Agents Python README](https://github.com/openai/openai-agents-python#readme) | 核心概念导航之后分环境设置与首次运行；最小文本示例给输出形态；明确密钥前提，把额外能力标为可选。 | 最小本地演示配预期结果字段；标准库基础流程与 DSH/Node 接入分层，不引导用户安装无关框架。 | 仓库页面标示 MIT。自行编写本项目示例，不复制源代码。 |
| [Claude Code README](https://github.com/anthropics/claude-code#readme) | 首段解释场景；按系统分安装入口并标记旧方式；启动步骤明确项目目录；独立列反馈、数据收集和隐私章节。 | 明确命令执行目录和平台差异；错误反馈说明可提交哪些诊断信息，以及先移除敏感记忆和密钥。 | [LICENSE.md](https://github.com/anthropics/claude-code/blob/main/LICENSE.md) 写明保留所有权利并受商业条款约束。仅参考抽象结构，不复制文本或素材。 |
| [Gemini CLI README](https://github.com/google-gemini/gemini-cli#readme) | 所读版本在安装前给访问资格与工具选择表；之后提供安装方案及 stable/preview/nightly 的风险区分。此处不独立核验其资格政策。 | 用二行路径表区分“独立 Python 使用”和“DSH 插件接入”；仅说明实际存在的交付与支持方式，不虚构发布渠道。 | 仓库页面标示 Apache-2.0。若将来复用具体文件，须核验文件许可和通知；本次仅借组织方式。 |

### 优先改进

1. **可核验的本地首用闭环。** 在首页前半部分给出前置环境、演示目录、非敏感中文样例写入、检索、预期结果和落盘位置，再展示重新运行后如何读取。命令须对本项目实测；没有计时证据不写“30 秒上手”。
2. **简短的边界与信任说明。** 解释适用/不适用场景、Python 与 Node 职责、记忆与索引位置、联网与遥测行为、备份/删除和故障自检。每项必须由实现支持，不把“本地保存”扩大成所有数据始终不出本机。

### 方法与限制

- 这是原创结构分析，不是对这些项目质量或安全性的排名。
- 没有根据 stars、品牌或下载量推断 README 会带来增长。
- 页面和许可可能变化；链接指向可变分支，不是固定提交快照。采用具体内容前应再次核验。
- 许可列用于提示核验边界，不是法律意见。此次没有引入第三方代码、截图、标志或成段文案。

## 本地优先与开发者体验：8 项目 README 研究

检查日期：2026-10-31。研究方式：直接读取公开 GitHub README 正文及必要许可证，仅分析文档与设计模式；未安装、运行或验证项目功能，未复制其代码。README 中的能力声明不等于本次实测结果，星数不作为设计有效性的证据。以下适配建议面向小型中文本地优先记忆插件 dsh-memory-system，不表示这些能力已经实现。

项目名称校正：QMD 的公开仓库是 `tobi/qmd`；Joplin 是 `laurent22/joplin`。共计 8 个不同项目。

| 项目／读取来源 | 直接观察到的文档结构与声明 | 建议适配（非已实现声明） | 许可与适用边界 |
|---|---|---|---|
| [QMD README](https://github.com/tobi/qmd/blob/main/README.md) | 开头介绍本地文档检索并展示 Mermaid 检索流程；Quick Start 串起 collection、context、embed、search、get；列出 JSON 输出和 status。 | 中文首页展示保存偏好、检索、读取来源、检查状态的完整闭环和真实预期输出；结果附来源 ID、上下文、匹配原因；保留不依赖模型的关键词基础路径。 | [MIT](https://github.com/tobi/qmd/blob/main/LICENSE)。本次只借鉴交互；未来若复用实现，须检查声明及依赖许可。 |
| [Vanna README](https://github.com/vanna-ai/vanna/blob/main/README.md) | 首页写清自然语言到 SQL 到答案的转换；演示、架构图、示例数据入口靠前；序列图展示身份传到工具执行层；结果说明包含进度、表格和摘要。 | 以会话、提取、本地存储、有来源召回解释系统；在读写边界明确 project/namespace，而非只靠提示词隔离。 | [MIT](https://github.com/vanna-ai/vanna/blob/main/LICENSE)；[仓库页面](https://github.com/vanna-ai/vanna)显示 2026-03-29 已归档，仅作设计参考，不推荐成为新依赖。 |
| [RAGFlow README](https://github.com/infiniflow/ragflow/blob/main/README.md) | 提供中文入口和目录；分块演示、架构、本地部署独立；功能文字强调分块可查看、人工干预和来源引用；部署前列硬件条件。 | 显示记忆原始证据、抽取内容和状态，允许修正；说明最小环境、存储位置和可选能力；不照搬大型部署栈。 | [Apache-2.0](https://github.com/infiniflow/ragflow/blob/main/LICENSE)；未来复用须检查 LICENSE/NOTICE。其 README 推荐起步 16 GB RAM 等，不宜直接移入小插件。 |
| [AnythingLLM README](https://github.com/Mintplex-Labs/anything-llm/blob/master/README.md) | 首屏本地使用定位、桌面下载、聊天 GIF；功能按用户行为列出，标明部分仅 Docker 版本支持；链接自动和人工管理记忆。 | 区分自动候选记忆与用户确认记忆；增加列举、修改、删除示例；能力表标注当前支持、可选、未实现。 | [MIT](https://github.com/Mintplex-Labs/anything-llm/blob/master/LICENSE)。本地运行不等于所选模型均离线；应说明远程模型的数据传输边界。 |
| [Khoj README](https://github.com/khoj-ai/khoj/blob/master/README.md) | 简短定位后列文档类型、客户端、本地和在线模型；演示后分别提供 self-host 与 cloud 路径；FAQ 回答输入格式、部署及自定义。 | 提供完全本地、无需 API key 的最短路径；中文 FAQ 回答格式、保存位置、离线可用性、删除方法；区分私有部署和云服务。 | [AGPL-3.0](https://github.com/khoj-ai/khoj/blob/master/LICENSE)。仅借鉴信息架构，不搬代码或文案。 |
| [Joplin README](https://github.com/laurent22/joplin/blob/dev/README.md) | 首段说明 Markdown、搜索、标签和外部编辑；解释 offline-first 为设备始终持有数据且断网可读，之后才讲同步及加密。 | 将本地优先变成可验证约定：断网保存和检索测试、可读导出、明确数据路径；优先备份恢复而非云同步。 | [根许可证](https://github.com/laurent22/joplin/blob/dev/LICENSE)为 AGPL-3.0-or-later，子目录许可可覆盖；logo/icon 另有限制，不借用品牌素材。 |
| [Logseq README](https://github.com/logseq/logseq/blob/master/README.md) | 区分 DB beta、移动端和 RTC alpha；明确可能丢数据，推荐备份及非关键测试 graph；另列入门、插件 API 和开发入口。 | 记忆格式升级提供版本号、备份和恢复说明；实验能力显著标注；示例用独立临时记忆库，避免污染真实数据。 | [AGPL-3.0](https://github.com/logseq/logseq/blob/master/LICENSE.md)。仅借鉴风险沟通和迁移思路，不移植代码。 |
| [Continue README](https://github.com/continuedev/continue/blob/main/README.md) | 当前 README 明确只读、不再维护、final 2.0.0；按 CLI、VS Code、JetBrains 分组并链接源码；说明最终版移除匿名遥测和认证。 | 核心 CLI 与宿主插件入口分开，每个入口提供一个验证方法；公开支持状态、兼容版本和遥测行为。 | README 标示 Apache-2.0；已停止维护，仅作透明度与入口组织参考，不推荐新依赖。 |

### 优先建议

1. **先建立可验证的中文闭环。** 首页给一个完全本地示例：写入、检索、查看来源、修改或删除、检查状态，附来自本项目实际运行的输出。参考 QMD 的操作链和 Joplin 对离线含义的明确解释。
2. **让记忆可检查、可纠正、可恢复。** 为记忆保留来源、作用域、时间与状态；区分候选和确认；验证删除与备份恢复。借鉴 RAGFlow 的证据展示和 Logseq 的风险说明，不因此引入大型数据库或多代理框架。

这些是基于文档的适配判断，不是产品性能对比。归档、许可证和 README 内容均可能变化，后续实际引用或依赖集成前应重新检查具体版本与文件。
