# 共享计划事务与扩展接口

「共享计划」可读取当前共享安排、调整现有段的数量与精确工时、预览并保存。保存共享草稿不改变最近采用版。存在待处理问题时，须明确勾选待处理草稿；必要确认、硬冲突或未完成计算阻止正式采用，需求缺口本身不自动阻止。

多人编辑的保存请求包含基准修订、完整输入版本、最近采用版及操作 UUID。服务端重放操作并计算关联范围，独立的旧修订可以合并，冲突返回409和日期、线体、机种或需求周差异。输入变化后需查看差异并按最新资料重新预览。客户端提供的预览结果不能代替保存时的重新校验。

断网或响应丢失后，页面保留操作编号并只读。核实接口返回第一次提交结果，或原子作废不存在的键。作废后的旧请求不能写入。页面保留修改，由用户重新预览并发起新操作，不自动重发。

API 均位于配置前缀：

- `GET /plans/current`、`/plans/revisions/{n}`、`/plans/adoptions/{n}` 供查看者与编辑者读取当前状态或冻结记录。
- `POST /plans/preview` 接收 `{base_revision,input_version,latest_adoption,operations}`，在内存重放，返回计划、变更、关联范围、未分配量及完整校验。
- `POST /plans/save` 和 `/plans/adopt` 额外接收 `operation_id` UUID、`pending` 布尔值，只有编辑者可以提交。
- `POST /plans/verify` 接收 `{operation_id}`，需要当前编辑权。
- `POST /plans/evaluate` 保留独立的原始快照规则诊断，永不持久化。

`PlanService` 统一预览、提交、历史读取、首次接入与核实。`register_operation(kind,model,replay)` 注册经过类型验证的原子操作。重放函数接收计划、操作和冻结的 `EvaluationSnapshot`，返回 `Plan` 或 `ReplayResult`。后者可携带未分配量、问题与来源信息；未处理决策可设 `blocks_adoption:true`。`patch_plan` 按识别号修改指定段和切机，保留其他内容。`replace_plan` 是显式完整替换，仍受完整关联范围、事实与锁定检查。

完整输入版本使用 `AP_INPUT_HEAD.CURRENT_VERSION`。需求、参数、日历、确认、事实、锁定与重置上下文的任何变更都须在该守卫下推进版本。`AP_PLAN_CONTEXT` 在其产生的输入版本保存不可变上下文；参数或确认单独推进版本时，继承该版本之前最近的上下文。计划事务先锁计划头，再锁输入头，两者使用 `WAIT 10`，随后检查授权、输入与影响范围、重跑规则、写入修订/采用版/审计和幂等结果，一次提交。

`OraclePlanStore` 复用已有访问存储的连接池。`write_context_in_transaction`、`initialize_in_transaction` 使用调用方游标，绝不单独提交。`PlanService.initialize(InitializePlan,actor,now)` 包含唯一键保护，将初始输入、覆盖范围、事实及外部重置来源与初始待处理草稿一起提交；正式采用仍是另一个明确动作。`check_consistency()` 供启动自检调用，发现头指针、摘要或幂等关系不一致时抛出 `StoreUnavailable`，调用方必须停止业务写入。

快照使用规范 JSON、压缩 BLOB 和原文 SHA-256。采用版保留汇总关系行及完整精确快照，关系行、采用版、修订、上下文、快照和审计均由数据库触发器禁止 UPDATE/DELETE。修订保留父修订、用户基准、原子操作和来源信息，供后续重置更正与历史查询使用。

前端 `useSharedPlan()` 提供共享保存、采用、未知只读、核实、冲突、操作列表和本机草稿序列化接口。`localDraft()` 只返回操作及基准版本，拒绝把整份替换计划藏在本机记录中。本机草稿的完整保留期限、多标签锁与升级迁移由本机恢复功能管理。

真实 Oracle 测试须显式设置 PLAN_TEST 配置与 `AUTO_PLAN_TEST_ORACLE=1`，执行 `pytest tests/test_oracle_plans.py`。多数并发测试在当前 `AP_*` 业务表追加测试历史。首次接入测试使用 `tests.oracle_namespace.disposable_business_namespace` 创建随机 `AP_TEST_*` 业务对象，保留原有授权表，结束只删除该次列明的临时对象，不重置正式测试历史。浏览器专项测试使用 `AUTO_PLAN_BROWSER_TRANSACTIONS_FIXTURE=1 npm run test:e2e -- shared-plans.spec.ts`；它注入本机持久化边界，不能证明 Oracle 并发。
