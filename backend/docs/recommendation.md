# 缺失资料双向推荐：v4-B 实验接入

本模块将已训练的 **JobRec v4-B** 两个LightGBM模型接入Django/DRF。省略或为`null`的资料保留为未知，转换为模型输入`NaN`；不补零、不调用LLM、不重新训练、不联网下载模型，也不保存请求资料。

这是可调用的实验接口。**v4-B没有通过预先设定的整体效果门槛**，尤其岗位侧存在退步。接口始终返回`experimental: true`和`release_gate_passed: false`。它不提供录用概率、资格判定或自动招聘决策；资料完整度也不是预测置信度。

## 安装与入口

在`backend/`按主README安装依赖和启动ASGI服务：

```powershell
python -m pip install -r requirements.txt
python -m pip check
python -m uvicorn config.asgi:application --host 127.0.0.1 --port 8765 --ws websockets-sansio
```

沿用项目的`DJANGO_SECRET_KEY`与本机访问配置。推荐接口本身不需要OpenAI、Kaggle或其他服务密钥，不涉及数据库迁移。后端清单固定LightGBM 4.6.0、NumPy 2.0.2和SciPy 1.14.1；根目录终端MVP依赖不因此改变。

| 方法 | 路径 | 排序方向 |
|---|---|---|
| POST | `/api/recommendations/jobs/` | 一个候选人的岗位列表，按`pref_score`降序 |
| POST | `/api/recommendations/candidates/` | 一个岗位的候选人列表，按`qual_score`降序 |

请求体为JSON。每次提供1至100个待排序对象，ID必须唯一；列表不自动去重或截断。接口返回全列表，前端可取前K项。100是新接口的资源上限，不是训练候选池或评分阈值。同分保持请求顺序，顺序差异不代表分数有差别。

这些接口沿用现有本机来源限制，没有新增远程用户鉴权；提交到Git远程不等于部署公开服务。

## 调用示例

用户只填写技能，也可以发起请求；此例没有GPA、经验和时间信息：

```powershell
$body = @{
  candidate = @{
    candidate_id = 'candidate-1'
    skills = @('python', 'sql')
    gpa = $null
  }
  jobs = @(
    @{ job_id = 'job-1'; required_skills = @('python', 'sql') },
    @{ job_id = 'job-2'; required_skills = @('java') }
  )
} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:8765/api/recommendations/jobs/' `
  -ContentType 'application/json' -Body $body
```

招聘方排列候选人：

```json
{
  "job": {
    "job_id": "job-1",
    "required_skills": ["python", "sql"],
    "min_months_experience": 6
  },
  "candidates": [
    {"candidate_id": "candidate-1", "skills": ["python"], "months_experience": 12},
    {"candidate_id": "candidate-2", "skills": ["python", "sql"], "months_experience": null}
  ]
}
```

响应外层包含`model_id`、`experimental`、`release_gate_passed`、`score_type`、`qualification_target`、`direction`、`sorted_by`和`results`。其中`score_type`固定为`uncalibrated_ranking_score`，`qualification_target`为`synthetic_per_job_winner`。

每条结果包含：

| 字段 | 含义 |
|---|---|
| candidate_id / job_id | 输入配对身份 |
| status | `scored`或`insufficient_evidence` |
| rank | 当前列表中的1起始名次；依据不足时为null |
| pref_score / qual_score | 两个独立模型的原始排序分数，可能为负；不能直接相加或解释成概率 |
| available_feature_count | 11项派生特征中已知的数量，不是置信度 |
| missing_features | 未知的派生特征名称，不因缺失直接判断不匹配 |

若一个配对的11项特征全部未知，结果置于列表末尾，两个分数和名次为`null`，状态为`insufficient_evidence`。这是显式输入边界，不使用常数分数伪造推荐，也不切换备用模型。只有少量特征可用时可以打分，但不保证有足够区分能力。

## 输入资料与特征计算

仅身份字段必须提供；下面各资料字段均可省略或设为`null`。字段名、列表项保留大小写和空格，使用原实验的精确匹配，不做同义词扩展、大小写转换或GPA换算。文本项不可为空白，最多512字符；列表最多256项。数字必须是JSON数值，布尔只能是JSON布尔，不接受字符串`"3.5"`或`"true"`。

| 候选人字段 | 岗位字段 | 派生特征 |
|---|---|---|
| skills：字符串列表 | required_skills：非空字符串列表 | 覆盖的岗位技能数/岗位技能总数 |
| interests：字符串列表 | industry：字符串 | 行业是否属于兴趣列表 |
| majors：已知专业列表 | acceptable_majors：字符串列表 | 是否有专业交集 |
| in_person_commitment：工作方式类别 | job_in_person_commitment：同类类别 | 双方字符串是否一致 |
| gpa：非负数 | min_gpa：非负数 | 候选人−要求，双方必须采用同一GPA尺度 |
| months_experience：非负数，月 | min_months_experience：非负数，月 | 经验月数余量 |
| academic_level：阶段代码 | min_academic_level：阶段代码 | 原阶段编码余量 |
| hours_per_week：非负数，小时/周 | min_hours_per_week：同单位 | 每周可投入时间余量 |
| length_of_commitment：非负数，月 | min_length_of_commitment：同单位 | 承诺时长余量 |
| num_publications：非负整数 | min_num_publications：非负整数 | 发表数量余量 |
| commit_to_summer：布尔 | 无 | 已知时为0/1，未知为NaN |

候选人ID字段为`candidate_id`，岗位ID为`job_id`。工作方式使用训练数据的`In Person`、`Online`、`No Preference`或`Hybrid`字符串，不能传布尔。原公式把`No Preference`作为普通类别精确比较，不将其解释为匹配所有岗位；本次保留该训练行为。学业代码顺序固定为`UG1, UG2, UG3, UG4, MS1, MS2, PhD1, PhD2, PhD3, PhD4, PhD5`，不自动将岗位`seniority`映射到学历。`majors`对应研究数据中本科、第二和硕士已知专业的集合，不应填入猜测的专业。

已知技能/兴趣/专业列表为`[]`表示明确为空，可得到零匹配；`null`表示未知。岗位`required_skills: []`仍按训练协议拒绝，不能用它代指未填写。岗位“没有要求”与“未采集要求”需调用者明确区分，未知不能擅自填成0。

现有`shared/contracts/CandidateProfile`主要保存技能与项目，`JobProfile`主要保存标题、领域和能力权重。这些契约没有全部实验字段，本次不修改共享契约或面试流程。调用者提供有据可查的资料；未采集的字段保持null。尚未自动把PDF/文本简历接到此接口，模型也未直接使用项目文本和面试评分。

## 错误与可观测性

- HTTP400：非法类型、NaN/Infinity字面量、负数、未知学业代码、额外字段、重复ID、空池、超限池、空岗位技能要求或float32数值范围溢出。错误返回字段路径/类型，不回显原始输入。
- HTTP503：模型清单、哈希、LightGBM版本、输入维度或树数不符，模型文件缺失/损坏，或推理失败。没有自动重试或备用评分。
- HTTP403/405：沿用本机来源限制；接口只支持POST。
- 日志记录模型版本、方向、配对数量、可评分与依据不足数量及错误阶段，不记录简历正文、技能、GPA或请求ID。

模型初始化和预测在进程内使用锁串行执行，CPU推理固定两线程；多进程部署每个进程独立加载。成功加载后缓存，更新权重需要明确发布新版本并重启。HTTP不能指定权重路径或模型版本。

## 模型来源、版本和局限

模型文件在`interviews/recommendation/artifacts/jobrec-v4-B/`，`manifest.json`保存输入顺序、权重SHA256、实验结果和来源。`pref.txt`为100棵树，`qual.txt`为20棵树；随模型的`.gitattributes`禁止Git转换权重换行，保证跨平台字节校验。权重是明确发布的运行资源，不包含原始简历、账户凭据或完整研究输出。

训练来源为2026-09-22的私有Kaggle实验[IRS JobRec Missing V4](https://www.kaggle.com/code/heropig/irs-jobrec-missing-v4)第1版；数据源为[DualOptimization_jobrec](https://github.com/brycekan123/DualOptimization_jobrec)，固定提交`c742feea3730e8d34ec2e8ae7e58c8c0ee8a53fd`。保留seed42和70/15/15岗位划分；候选人允许跨划分；仅在有标签的候选池上评价。

训练资料按实体分配：50%概率完整、25%随机隐藏30%资料块、25%隐藏GPA/发表数量/每周投入时间/承诺月数/暑期投入。无标签值保持未知。每个任务按clean、random_30、optional_bundle三种验证场景的AP均值选择树数。原始指标和门槛摘要已保存在manifest中。

| 指标 | 历史完整训练模型H | 当前接入B |
|---|---:|---:|
| 完整资料偏好AP | 0.8430 | 0.8372 |
| 随机缺失30%资料偏好AP | 0.4947 | 0.6010 |
| 三个主缺失场景偏好平均nDCG@5 | 0.8013 | 0.8029 |
| 三个主缺失场景岗位侧平均nDCG@5 | 0.3942 | 0.3425 |

主缺失场景偏好增益仅0.00153，95%查询bootstrap区间为[−0.00609, 0.00929]，未达到0.03门槛。岗位侧完整资料AP下降0.0966。接入是用户明确要求的实验能力发布，不表示该模型比历史模型全面更好。

偏好标签为合成偏好；岗位侧标签每个岗位只选一个合成赢家，不是所有合格者标签。岗位侧测试仅15个正例；测试集已反复用于开发诊断。真实漏填机制、全库召回、真实录用效果及公平性未验证。

本研究未确认上游数据具有明确许可，因此不替上游授予数据或衍生模型的商业使用权；分发清单保留这一事实，不附带原始档案。

## 验证

```powershell
# 在backend目录、已安装开发依赖且已设置测试用DJANGO_SECRET_KEY时执行。
python manage.py test interviews.tests.test_recommendation
python tools/check_docs.py
```

测试直接加载实际权重，重放五个缺失场景的20组冻结研究预测，并验证两种HTTP排序、缺失与零区别、依据不足、稳定并列、输入边界、错误脱敏和损坏文件503。没有用mock模型替代正常推理；损坏测试仅改临时副本。

研究阶段曾修正一处完整度分层报表ID错误；权重、输入、标签、选择轮次和预测未变。本次打包的B模型来自已在本机重放200组预测、最大差值为零的结果。

### 本次接入核验记录（2026-09-24）

- 从远程基线`caaf6e4`建立功能分支；未修改共享契约、面试算法或训练条件。
- 使用固定提交、哈希校验后的原始资料，重建47,029个完整配对及40个场景各6,966个测试配对：本模块生成的特征与已验证v4产物逐项一致，包括NaN位置。
- 新增11项推荐测试通过；后端全部92项测试通过；根目录191项测试及5项subtests通过。
- 修改范围Ruff及Python语法检查通过；依赖兼容性检查通过。
- 全仓Ruff仍有基线已有的`backend/interviews/demo.py:3`行长问题，未因本次修改增加问题。
- 实际运行`python tools/check_docs.py`：当前Anaconda 3.12.7环境在扫描过程中原生访问违规；在独立CPython 3.11.9环境、同一检查器和固定Tree-sitter依赖下成功完成扫描，返回6条既有问题。用未修改基线独立复核得到相同6条问题，涉及`agent_socket.py`、`agent_fixtures.py`、`test_agent_persistence.py`。本次新增代码没有检查问题，但不将全仓检查称为通过。
- 人工核对了新增职责、函数目录、模块变量索引和输入/错误语义。代码、注释、权重、依赖与说明作为同一提交交付；未把本地环境、原始资料、密钥或运行日志纳入提交。
- 真实模型通过DRF请求测试；未部署公开服务器、未实现推荐前端或自动PDF字段抽取，未声称真实招聘效果已验证。
