# Bonding 排程求解方案：规则表达、证明边界与验证计划

研究日期：2026-10-07（北京时间）。研究范围：[哪些求解方案能表达已定排程规则，并如何验证 30 秒／2 分钟目标？](https://github.com/Leegerger/auto_plan/issues/13)。本文是研究资产，不是生产实现、部署决定或性能达标报告。来源限定为项目已确认决议、求解器官方文档与官方源码；文中建模方案和实验门槛是研究建议，另有标注。

## 1. 结论及当前证据强度

建议进入一个可丢弃的技术验证原型：以 **OR-Tools CP-SAT 与一条 MIP 路线并行对照**，二者共享输入契约与独立验证器；再比较“从当前草稿出发的构造／修复 + 求解器邻域改进”是否更快给出有效候选。CP-SAT 的离散逻辑和顺序表达适合这类问题；HiGHS 提供开源 MIP 基线；SCIP 可作为开源复杂模型及精确 MILP 复核路线；有可用商业许可时，可加入 Gurobi 对照。这个排序是根据规则结构作出的技术推断，**不是速度排名或最终选型**。

现在不能说任何方案已完整、无损地表达全部业务，更不能说 18 条线、约 40 个机种、105 个生产日一定能在 30／120 秒内完成。真正影响路线的前置条件包括：

1. **Recipe 分配工时 H 的精度未定。** 保养、实验是整数小时，切机输入最多两位小数，并不意味着 H 是整数小时或 0.01 小时。容量向上取整在连续 H 下含严格不等式；擅自离散化会排除原规则允许的计划。
2. **投入归属需求周、确认额度与合并产出尾差的衔接需用例消歧。** 总产出取整及较早周优先已定；不能为方便建模按需求周、确认批次或线体重新取整。
3. **真实样本、目标设备与运行环境尚未确定。** 顺序段数、可生产关系密度、确认批次数、锁定密度和月界，比“18×40×105”这一乘积更能决定难度。
4. **词典序最优证明可能比找到可行计划难得多。** 可行结果、已认证的目标前缀与未完成搜索必须分别报告。

已查明官方能力：CP-SAT 有整数约束、顺序建模、提示解、限时、异步停止与解／界信息；HiGHS 当前官方开发版文档已有原生词典序多目标；Gurobi 有原生层级多目标及每阶段参数；SCIP 10 有额外依赖的 numerically exact MILP 模式。上述均需在原型选定的**正式版本与对应 API**中做冒烟测试。开发版文档、`stable`／`current` 链接不是已安装二进制的版本证明。[CP-SAT 概览](https://developers.google.com/optimization/cp/cp_solver)、[HiGHS 多目标与热启动](https://ergo-code.github.io/HiGHS/dev/guide/further/)、[Gurobi 多目标](https://docs.gurobi.com/projects/optimizer/en/current/features/multiobjective.html)、[SCIP 精确模式](https://www.scipopt.org/doc-10.0.0/html/EXACT.php)。

## 2. 本研究不能改写的业务契约

权威细节仍只保存在对应决议；本节是研究覆盖范围索引。

| 规则来源 | 对求解和验证的约束 |
| --- | --- |
| [投入、产出与周出货需求如何按时间和良率核算？](https://github.com/Leegerger/auto_plan/issues/2#issuecomment-5963611695) | 北京时间 08:00 生产日；十五周窗口；整数投入；同机种同投入日先跨线合计，CT 分摊后逐笔向上取整；较早需求周优先，迟交不追改按时满足；在制品单独计产出。 |
| [Recipe 与线体如何决定可生产能力和切机耗时？](https://github.com/Leegerger/auto_plan/issues/3#issuecomment-5979865250) | 固定机种→Recipe；日×线×Recipe 汇总 H 后只取整一次，投入上限 `ceil(C×H/24)`；有方向切机；可拆批／跨日／暂停；未知起始 Recipe 特例。 |
| [Holiday、保养和实验如何形成线体可生产时间？](https://github.com/Leegerger/auto_plan/issues/4#issuecomment-5980162688) | 系统只有每日容量，没有停机起止时刻；Holiday 例外仅改变投入能力；产出统一全厂日历；切机现场连续性不由系统证明；实际切机日占用不能重复扣除。 |
| [跨周生产需要怎样的物料与出货确认？](https://github.com/Leegerger/auto_plan/issues/5#issuecomment-5985910923) | 物料批次约束投入量和最早投入日，出货批次约束合格产出量和最晚产出日；批次数量为新增额度；未确认只能待确认预排，明确不允许则禁止。 |
| [旧计划变化率与远近周调整容忍度如何定义？](https://github.com/Leegerger/auto_plan/issues/6#issuecomment-5986122802) | 最近采用版是稳定性基线；仅同一尚未执行范围；实际日期对齐；汇总数量锁定含零锁定，不锁顺序；容忍度不是硬约束。 |
| [需求与参数如何维护、校验并保留历史口径？](https://github.com/Leegerger/auto_plan/issues/7#issuecomment-5987384090) | 产能和双良率按投入生产日月份；在制品用重置周一月份；良率百分数与 CT／切机各有精度约定；运行使用版本化快照。 |
| [新增周自动排程与局部调整如何权衡目标并处理无解？](https://github.com/Leegerger/auto_plan/issues/8#issuecomment-5987706709) | 逐周目标、切机次数优先于稳定性；范围外数量与顺序固定并检查边界；富余边界、候选采用、限时和结果状态已定。 |

设 `O_w` 为需求周 w 的按时缺口合计，`F_w` 为原需求周 w 的最终未补足缺口合计，`K` 为切机次数，`D` 为计划变动代价，`R` 为保持原日内顺序的最后偏好，比较向量必须是：

`(O_1,…,O_15, F_1,…,F_15, K, D, R)`。

这是最多 **33 层**，不是把 `(O_w,F_w)` 每周交错，也不是总体缺口加权和。同层跨机种按片数相加；先比较所有按时缺口，再比较所有最终缺口。`D` 默认近期片数绝对变化×3、远期×1，移出和移入各计；无基线新增周不计，已有零基线新增量计。切机时长是容量数据及解释指标，不能替换 `K`。最后的顺序偏好不能影响任何前项；其具体距离函数应在原型用拆段、删段案例说明，尚不能宣称某个任意编辑距离就是既定业务公式。

## 3. 精确算术：哪些能直接编码，哪些仍需决策

### 3.1 投入与产出可使用精确有理数／整数关系

以下是由已定公式推导的等价关系，不是新增业务规则。

双良率分别存为整数 `a/10000`、`b/10000`；CT 小数部分存为 `t/100`。对同机种同投入日各线合计 `I`，第一笔、第二笔未取整分摊量分别为：

`I*a*b*(100-t)/10^10` 和 `I*a*b*t/10^10`。

`t=0` 时只生成第一笔。对任何非负整数分子 `N`、正整数分母 `B`，整数 `Y=ceil(N/B)` 精确等价于：

`B*Y - B + 1 <= N <= B*Y`。

也可以用 `N = B*Y - r, 0 <= r <= B-1`；不能只写 `Y >= N/B`，因为优化需求时可能把 Y 虚增。先对已知常数约分，给每个变量严格有限的安全上界，再检查**整个线性式的最大绝对值和中间乘积**，不能只检查最终解。CP-SAT 原生系数、变量域和线性式受 int64 范围及模型校验约束；若缩放溢出，应分解、约分或改表达，不能静默截断、用浮点产出代替。[CP-SAT 模型定义与溢出校验说明](https://github.com/google/or-tools/blob/stable/ortools/sat/cp_model.proto)。

日期映射可在输入快照中预先计算，求解器只选数量：实际投入日为 D0，Dk 为其后第 k 个非 Holiday 日；按 n 与 n+1 分摊，落在 Holiday 的那笔再顺延。两个分摊槽即使落到同一天也必须保留独立身份。窗口外产出日期必须延伸到最后一笔真实产出，不凭空截在第 16 周；只禁止窗口外**投入**。

独立核对例子：

- 全日产能 1000、汇总 H=10 小时，上限 417；拆为两个机种各 5 小时仍共同上限 417，不能变成 209+209=418。
- G=931、CT=0.5，各笔为 466，总数 932；两笔顺延至同一天仍为 932。
- 不同投入日各产生 100.2，分别取整后为 202，不能合并为 201。
- 10 月 31 日投入使用 10 月产能和良率；11 月产出不更换该来源的良率。

这些是规则算术核对，不是求解器完整模型或性能测试。

### 3.2 容量向上取整不能用普通线性耗时倒算替代

令 `q` 为日×线×Recipe 的整数投入，`C>0` 为全日产能，`H>=0` 为该 Recipe 汇总生产工时。对 `q>=1`：

`q <= ceil(C*H/24)` **等价于** `C*H > 24*(q-1)`。

注意是严格大于。`H=0` 时 `q=0`；不能误用 `C*H >= 24*(q-1)`，否则零工时可生产 1 片。也不能改成 `H >= 24*q/C`，后者会丢掉既定向上取整允许的数量。

如果以后明确 `H=h/B`，h 为整数刻度，则可用 `q<=ceil(C*h/(24B))` 的整数等价关系；或对正 q 使用 `C*h >= 24B*(q-1)+1`。这是**在该网格被业务确认或证明无损以后**的等价模型，不能先默认 B=100。MIP 可以有连续 H，但通常不支持严格不等式；任意选一个 epsilon 同样会改变边界，浮点可行性容差可能吞掉 epsilon。Gurobi 官方明确只支持非严格比较，并以容差判定数值可行性。[Gurobi 约束与严格不等式说明](https://docs.gurobi.com/projects/optimizer/en/current/concepts/modeling/constraints.html)、[数值容差与缩放](https://docs.gurobi.com/projects/optimizer/en/current/concepts/numericguide/tolerances_scaling.html)。

**0.01 小时网格的反例：** 一条线一天 T=1 小时；两个 Recipe 的 C 均为 1000；初始 Recipe 未知；R1→R2 切机为 0.95 小时；各投入 2 片。连续 H1=H2=0.025 时，分别 `ceil(1000×0.025/24)=2`，总工时正好 1，可行。0.01 小时网格则二者至少各 0.03，总计 1.01，不可行。输入 S 精确到 0.01 并不能推出 H 同样离散。该反例已用 Fraction 算术和网格枚举核对。

连续时间并非必然“无法精确建模”。例如单日、切机日占用已经固定为 S、没有其他时序限制的子问题，若存在正投入 Recipe 集合 A，则连续工时可分配的充要条件为：

`sum(24*(q_r-1)/C_r for r in A) < T-S`。

这是因为每个 H_r 都须严格超过其下界；总和有正余量即可分配。空 A 则只检查 S≤T。所有 q、C 和 S 都有有理数表示时，可在有限范围枚举容量模式或用精确有理数判定；特定模型还可能消去 H 后得到整数条件。但跨日切机分配、顺序和边界会增加耦合；清除分母的最小公倍数可能巨大。**不能把这个单日推导当成全模型无损整数化已经完成。** 原型应先证明所用转换，再检查整数范围、模式数量和运行成本。

对完整小 oracle，还有一条不先固定时间网格的核对路线：固定数量、顺序等离散选择后，对有限个严格线性约束 `a_i x>b_i` 引入辅助变量 `0<=e<=1`，改为 `a_i x>=b_i+e` 并精确最大化 e；存在 e>0 当且仅当原严格系统可行。e 是该实例的证明辅助量，不是固定的最短生产时间。也可用支持数学 Int／Real 与严格比较的 Z3 对小实例建精确线性参考模型；这只是 oracle／候选核对选项，不追加全规模性能推荐。候选固定后不可行不能推导所有离散安排不可行。[Z3 官方算术说明](https://microsoft.github.io/z3guide/docs/theories/Arithmetic/)。

### 3.3 需补齐的输入归属与尾差衔接

已定规则唯一确定了“同机种同投入日先合计，再 CT 分摊和取整”和“较早需求周优先”。不能重开这两项，也不能为了获得更多产出而按需求周或确认批次再调用一次 `ceil`。

尚需用少量案例明确：同机种同投入日的投入同时用于不同需求周时，**投入如何归属各周以消耗物料批次额度，以及这份归属如何与合并后产出、FIFO 覆盖和必要尾差一致**。不能把出货额度当投入额度，不能仅给每个变量各自设上限而漏掉两条账之间的连接。

一个接近业务数量的边界例：双良率乘积 93.1%、CT=0.5，且两笔均按时产出。需求 1000 的名义所需投入为 `ceil(1000/0.931)=1075`；但 I=1072 时 G=998.032，两笔499.016各自上取整为500，实际预计合计也为1000；I=1075 则两笔500.4125各上取整为501，合计1002。这两组数已用 Fraction 核对。更小的放大例是双良率50%、CT=0.5，投入1片产生 `ceil(0.25)+ceil(0.25)=2` 片，而需求2的名义投入是4。公式各自清楚，**求解时“所需投入量”是必须达到的归属目标，还是名义估算而最终以逐笔产出覆盖判定投入充分性**，现有决议没有把两者合并为一个唯一的优化约束。跨月良率、一个投入日覆盖多周、某一批仅允许提前 1 片时，会使选择影响正式可行性。

建议后续访谈只消歧这一衔接，以及必要尾差由哪个需求归属承担；不默认逐片物理血缘或引入新库存模型。技术原型可先做“单需求周／无跨周确认”等无歧义子集的独立公式测试，完整候选验证须等这份契约明确。不要用随意追加“总投入最少”作为第六目标来偷偷补这个业务空白。

## 4. 共用模型与独立验证边界

以下是对任一求解器都适用的建模建议。

### 4.1 数量、状态和顺序分开表达

- 主数量变量 `q[d,l,m]` 是非负整数，只在获准修改、可生产且有能力的组合建立自由变量；数量锁定对汇总 q 建等式，含零锁定。范围外数量固定，范围外顺序固定。已执行事实独立存放，不能仅按“过去日期”判成已执行。
- 日×线×Recipe 的总生产 H 与 q 聚合；生产段只决定顺序和状态，**每段不得单独赚取一次容量上取整**。
- 每条线保留 Recipe 状态，空闲、Holiday 和保养沿用状态。不同 Recipe 的相邻转移计一次 K，S=0 或完全用普通空闲完成也计。未知起点第一次不计，随后状态已知。
- 明确记录一项切机事件、源／目标 Recipe、各生产日实际占用和完成关系。分摊工时之和等于这次 S，同一普通空闲工时只消费一次；已在前日付出的切机不能次日再扣。提前切机只能占正常可用且当时未被生产或其他切机占用的工时。
- 同一 Recipe 可在一天回访，例如 R1→R2→R1，不能默认“一天一个 Recipe”或“每 Recipe 每天一段”。若用有限槽位上限，必须证明在本数据域中无损，否则标成限制模型，只用来找候选，不用于宣称业务无解／全局最优。零耗时转移还使“以总工时除最短切机时长算段数上界”不成立。
- 局部模型必须带入左侧已知状态、右侧固定生产及所需衔接，并继续检查传播到后续固定安排的容量。固定一两天缓冲不天然足够；跨多日空闲或提前切机时，应沿状态和切机事件传播到影响消失处。

CP-SAT 官方样例用 `Circuit` 邻接弧、条件先后关系和 `NoOverlap` 表达带序列相关间隔的机台顺序；证明了这些基础组件存在。这里的日级容量模型仍需自行拼接，**不得直接套用该 job-shop 样例并声称已有停机连续时段检查**。MIP 可用二进制邻接／位置变量和线性连接；没有任何一款求解器内置本项目的 Recipe 规则。[OR-Tools 官方带换序间隔样例](https://github.com/google/or-tools/blob/stable/examples/python/jobshop_ft06_distance_sat.py)。

### 4.2 产出和确认必须保留可追溯流量

一笔预计产出至少保留 `(机种, 投入日, CT分摊槽, 产出日, 使用的月度双良率)` 身份。起始在制品另建来源。输出分配按原需求周记录按时覆盖、有效迟交补足、待确认假设覆盖及富余，数量不能重复分配。正式指标不能混入待确认假设。

物料确认可用批次到允许投入日期的配额流表达：只为 `d>=批次最早日` 的边建变量，每批所有线／日期合计不超其新增投入额度。出货确认则是迟交产出来源到批次的流：只为 `产出日<=该批最晚日` 建边，每批合计不超合格产出额度。不同批次日期不能取最宽边界后合并；相同机种跨线共用物料额度。前述投入到需求周的连接契约明确后，才可宣称整条流与业务等价。

“较早周优先”要成为分配规则或约束，不能只依赖目标。否则求解器可能把晚到的一批产出分给当前周，隐藏旧周缺口以改善当前周按时值；这不符合已定分配口径。没有合法出货确认的老周缺口如何与后周有效安排衔接，也应在同一归属用例中核对，不能自行放宽确认。

富余处理是已定候选有效性边界：授权范围内未执行、未锁定且已不覆盖窗口内需求的多余投入需去掉；范围外／锁定／执行事实的富余保留提示，必要舍入尾差保留。只设一个宽松“总投入最大值”不能证明无新增无需求生产，也不能用稳定性或切机目标抵销这条规则。原型须输出投入用途与尾差解释，并用减需求测试检验。

### 4.3 快照、热启动和验证器

一次运行快照包含当前草稿、最近采用版、执行事实、范围与锁定、全部参数／日历／确认版本，以及本次比较设置。候选从**当前草稿**启动，D 对比**最近采用版**。先独立检查草稿；草稿有冲突时不能把它当成已知可行 incumbent，只可作为部分提示或修复参考。求解器 hints／starts 既不等于约束，也不保证被接受，更不保证更快：CP-SAT 为 `solution_hint`，HiGHS 为 `setSolution`，Gurobi 为 `Start`，SCIP 为 `addSol`／`trySol`。[CP-SAT hint 定义](https://github.com/google/or-tools/blob/stable/ortools/sat/cp_model.proto)、[HiGHS MIP 热启动](https://ergo-code.github.io/HiGHS/dev/guide/further/#MIP)、[Gurobi Start](https://docs.gurobi.com/projects/optimizer/en/current/reference/attributes/variable.html#attrstart)、[PySCIPOpt 解 API](https://pyscipopt.readthedocs.io/en/stable/api/model.html#pyscipopt.Model.addSol)。

独立验证器直接读取候选与冻结输入，用整数／有理数重新计算；不接受求解器提供的预计算“总产出”“总耗时”“缺口”作为真值，不共用模型里可能有错误的线性约束生成逻辑。它必须复核：

1. 汇总数量、允许关系、执行事实、零锁定和范围外数量／顺序；
2. 每日容量、容量取整组、状态链、切机次数、跨日占用与重复空闲抵扣；
3. 月度参数、CT／Holiday 映射、逐笔取整、WIP 独立来源和窗口外产出；
4. 需求优先分配、两种确认批次及单位、有效／待确认覆盖；
5. 富余边界、33 层指标及逐周变化率；
6. 快照哈希和候选哈希，证明／搜索状态所针对的具体模型。

验证通过只证明这份候选符合所实现且已消歧的业务规则，不证明全局最优；系统仍不证明现场切机连续性。采用前若相关数据版本变化，应对新快照重校验并重算，由用户明确采用；任何自动完成、取消或补齐确认都不自动更新采用版。

## 5. 求解路线能力与代价

| 路线 | 可以据一手资料确认的能力 | 本任务的代价与限制 | 条件化用途 |
| --- | --- | --- | --- |
| OR-Tools CP-SAT | 整数／布尔线性与逻辑、表／自动机／顺序组件；提示解、解回调、界、时限及异步停止；内建 LNS。 | 连续 H 不能直接作为变量；需无损转换或经确认的网格。int64 范围需预检。多层目标建议由应用顺序求解。大量日线顺序弧可能膨胀。 | H 契约与转换通过小 oracle 后，作为首个离散排程候选。 |
| HiGHS MIP | 连续／整数线性优化；MIP 部分起始解；当前 dev 文档含词典序目标；限时、MIP 中断回调、改进解与 primal／dual bound。 | 所有顺序、ceil、确认关联要线性化；使用浮点数和容差。必须锁定实际版本验证多目标，不能默认 wrapper 暴露全部功能。 | 开源 MIP 基线；验证是否比 CP-SAT 的整数缩放更适合该数据形状。 |
| SCIP MIP／CIP | MIP 与约束整数规划；解注入、事件、停止、当前最佳解；可按阶段换目标。SCIP 10 可选精确 MILP 模式。 | 普通模式不是有理数证明；精确模式仅适用受支持的 MILP 与安全插件，依赖及成本更多；不能让“exact”自动修复错误业务模型或严格不等式语义。 | 开源第二路线，尤其用于小／中模型精确复核和需要扩展约束的研究。 |
| Gurobi MIP | 原生层级线性目标、MIP Start、限时、terminate、解和界、IIS 工具。 | 浮点容差与层级退化参数须审计；商业许可和平台条件；Big-M 过大／缩放差会伤害数值可靠性。 | 有实际许可、数据和设备时对同一 MIP 做对照，不能以品牌代替实测。 |
| 构造／修复启发式 + CP-SAT 或 MIP 邻域 | 根据草稿和约束构造候选，移日／移线／顺序邻域后使用完整验证器；求解器可完成邻域内搜索。 | 自建启发式无全局最优／不可行证明；滚动冻结远期、固定顺序或限制槽位均缩小搜索空间。局部子问题最优不等于十五周最优。 | 优先快速得到有效候选，保留完整模型继续改进或作界的来源；必须按全向量比较与记录认证范围。 |

能力依据：[CP-SAT 约束说明](https://developers.google.com/optimization/cp/cp_solver)、[CP-SAT 官方 LNS 参数](https://github.com/google/or-tools/blob/stable/ortools/sat/sat_parameters.proto)、[HiGHS 功能](https://ergo-code.github.io/HiGHS/dev/guide/further/)、[HiGHS 回调](https://ergo-code.github.io/HiGHS/dev/callbacks/)、[SCIP 框架](https://www.scipopt.org/doc/html/)、[PySCIPOpt API](https://pyscipopt.readthedocs.io/en/stable/api/model.html)、[Gurobi 层级目标](https://docs.gurobi.com/projects/optimizer/en/current/features/multiobjective.html)。最后一行是本研究提出的混合架构，不是官方对本业务效果的承诺。

模型尺寸应在每次实验记录。光数量张量上界已有 `18×40×105=75,600` 个组合；这只是输入尺寸推导，不是最终变量数。日线每 K 个候选段若全连接，顺序弧约 `18×105×K²`；确认分配再增加“来源×需求周×批次”边。优先稀疏建模，删除确实不可能的组合，保留 CT、月界和范围边界；任何为压缩规模引入的启发式删边都须声明可能损失可行解。

## 6. 词典序、限时、取消及证明的运行契约

### 6.1 推荐先实现可审计的分阶段控制器

每层目标均使用精确整数值或已确认且精确缩放的权重：

1. 以完整硬约束模型优化当前层，目标时限来自**同一个端到端总截止时刻**；不要给 33 层各分配 30 秒或 120 秒。
2. 每个候选通过独立验证，并用完整向量与已保留候选比较；保存最佳有效候选。
3. 仅在当前层最优值有充分证据时，将 `f_i=v_i` 固定后进入下一层；若已有候选达到已知理论下界 0，也可以认证这一层，无需等待额外搜索。
4. 若到时仅找到 v 但无最优证明，返回候选并标出“已证明前 k 层、当前层 incumbent／bound、余下层未证明”。可以在 v 不退化的受限空间继续改进较低层，但该结果是**以未证实 v 为条件的候选**；继续／延长求解时必须回到未认证层，不能把 v 当业务硬约束永久冻结。
5. 不能对现有向量每一个分量都加“不恶化”上限来代替词典序。例如 `(0,1000)` 优于 `(1,0)`；较前项改善允许较后项变差。临时等式只限制特定阶段子问题，候选档案始终用完整向量比较。
6. 词典序值相同的方案可做确定性选择；明显取舍最多保留三个供用户查看，默认推荐仍按已定向量。不得用额外多样性目标使默认方案在更高层变差。

不建议用超大权重一次近似编码 33 层。若欲使用混合进位，必须证明每层上下界、相邻整数步长和全部后项最坏总幅度，再检查整型／数值范围；这不是“足够大”的经验判断。

**状态不等于证明：** CP-SAT `OPTIMAL` 在设置 gap limit 后也可能表示“达到该 gap”，纯可行性问题找到解也会返回此值。用整数目标值及可信整数界核对，禁用造成提前认证的 gap 设置；若整数目标上界 U 与有效下界 L 间不存在更好的整数值，才可认证该层。不能从浮点显示值的四舍五入推导证明。[CP-SAT 状态与整数界字段源码](https://github.com/google/or-tools/blob/stable/ortools/sat/cp_model.proto)。

Gurobi 原生层级 MIP 的后续允许范围与 incumbent、bound、MIP gap 及 `ObjNAbsTol`／`ObjNRelTol` 有关；HiGHS 原生词典序也通过前级边界及容差推进。故先用显式逐层等式便于核对，之后再验证原生多目标能否替代。关闭 gap 容差不使浮点 MIP 自动成为任意实数意义的严格数学证明；候选可行性与目标最优性证据仍需区别。需要数值精确 MILP 认证时，可研究 SCIP 10 精确模式及证书复核，而非把 `MIPGap=0` 宣传为有理数证书。[Gurobi 层级退化规则](https://docs.gurobi.com/projects/optimizer/en/current/features/multiobjective.html#multi-objective-mip)、[HiGHS 词典序](https://ergo-code.github.io/HiGHS/dev/guide/further/#Lexicographic-optimization-of-multiple-linear-objectives)、[HiGHS 数值可行性](https://ergo-code.github.io/HiGHS/dev/guide/kkt/)、[SCIP 精确模式和证书](https://www.scipopt.org/doc-10.0.0/html/EXACT.php)。

### 6.2 官方停止能力与应用保存责任

| 求解器 | 时限与取消 | 可保留证据 |
| --- | --- | --- |
| CP-SAT | `max_time_in_seconds`；`CpSolver.stop_search()` 异步停止，解回调也可停止。 | 可行解、目标及 bound、状态、日志；应用逐次复制验证后的候选。 |
| HiGHS | `time_limit`；MIP interrupt callback 设置 `user_interrupt`。不能只等“发现新解”回调，否则无新解时取消无响应。 | improving-solution callback、`HighsModelStatus` 与独立 solution status、primal／dual bound／gap。 |
| SCIP | `limits/time`；`interruptSolve()` 尽快停止；`getBestSol()` 可能为 None。 | 最佳解、status、primal／dual bound；精确模式另有证书选项。 |
| Gurobi | `TimeLimit`；`Model.terminate()` 可从其他线程发起，官方明确不保证立即生效。 | Status、SolCount、incumbent 和 bound；检查是否真有解再访问解。 |

来源：[OR-Tools 限时](https://developers.google.com/optimization/cp/cp_tasks)、[CP-SAT 异步停止源码](https://github.com/google/or-tools/blob/stable/ortools/sat/python/cp_model.py)、[HiGHS options](https://ergo-code.github.io/HiGHS/dev/options/definitions/)、[HiGHS callbacks](https://ergo-code.github.io/HiGHS/dev/callbacks/)、[PySCIPOpt Model](https://pyscipopt.readthedocs.io/en/stable/api/model.html)、[SCIP 参数](https://www.scipopt.org/doc/html/PARAMETERS.php)、[Gurobi terminate](https://docs.gurobi.com/projects/optimizer/en/current/reference/python/model.html#Model.terminate)、[Gurobi TimeLimit](https://docs.gurobi.com/projects/optimizer/en/current/reference/parameters.html#parameterTimeLimit)。

本项目建议在独立 worker 进程求解，由监督进程维护截止时间、取消标志和原子保存的“最近验证通过候选”。建模、读快照、求解、回调校验、序列化和 UI 可用都计入等待时间。硬杀 worker 只能保留已经完整保存的候选；不能承诺保住尚在内存的最新解。求解器停止机制是协作式，取消确认时间与实际 worker 退出时间要分别测。回调内不做重型完整校验以阻塞搜索，可复制候选进入受控队列，最终展示前一定完整验证；丢弃中间候选时保留最后一个完整有效版本。

### 6.3 用户状态与证据映射

| 场景 | 可说的结论 | 不可说的结论 |
| --- | --- | --- |
| 存在独立验证通过候选，但仍有 O 或 F 缺口 | 已找到符合硬约束的候选，具体缺口明确；可按业务流程采用。 | “有缺口，所以计划无解”。 |
| 截止／取消时未找到有效候选 | 本次时限／搜索范围内尚未找到。 | “不存在任何排法”。 |
| 完整业务模型加“所有需求按时满足”约束后有可靠不可行证据 | 不能全部按时满足，标明约束和快照。 | “不存在部分满足的可执行计划”。 |
| 完整模型加“最终全部补足”条件后有可靠不可行证据 | 不能在有效确认内全部补足。 | 将其等同于不能按时／硬约束无解，或忽略原需求周。 |
| 完整、等价且已消歧的硬约束模型不可行 | 当前输入、范围和约束下无硬约束可行计划；列证据与受影响对象。 | 外推到扩大范围／修改确认后的所有业务状态。 |
| 保持前级最优值时，无法使最终全部补足 | 只说明在该前缀下不能全部补足；记录前缀约束。 | 即使前缀已证最优，也不能省略它并外推为原硬约束下不能全部补足。 |
| 邻域、槽数上限、固定启发式顺序、临时阶段等式之下不可行 | 该受限模型不可行，标明附加限制。 | 外推为完整业务无解。 |
| MODEL_INVALID、数值错误、许可证错误、内存／进程错误、INF_OR_UNBD | 计算失败或状态未分清；保存已有有效候选。 | 报业务无解或正常最优。 |

形式上，设 F 为业务硬约束、P 为阶段前缀、D 为全部满足条件，`F∧P∧D` 不可行不能推出 `F∧D` 不可行，即使 P 是正确的最优前缀。例如只有 `(按时缺口0,最终缺口1)` 与 `(按时缺口1,最终缺口0)` 两种排法，保持按时最优会留下最终缺口，但原 F 仍存在最终全补足方案。已有独立验证的可行候选即证明 F 非空，后续阶段报不可行不能抹去这份证据。

这些状态要由应用映射，不能仅显示求解器英文枚举。各引擎都区分不同停止原因；HiGHS 还区分 API 状态与模型状态。[CP-SAT 状态](https://developers.google.com/optimization/cp/cp_solver)、[HiGHS 枚举](https://ergo-code.github.io/HiGHS/dev/structures/enums/)、[SCIP 状态源码](https://github.com/scipopt/scip/blob/master/src/scip/type_stat.h)、[Gurobi 状态](https://docs.gurobi.com/projects/optimizer/en/current/reference/numericcodes/statuscodes.html)。

解释可先使用可复算的确定矛盾，例如锁定 q>0 但该日容量为零、范围外固定顺序造成边界切机无法容纳；再使用带业务名称的约束核心／IIS 辅助定位。CP-SAT assumptions 返回的是足够导致不可行的一组，不保证最小；MIP IIS 也不应解释为唯一根因或自动放宽许可。诊断模型应移除临时目标固定及启发式限制，保留日志和模型指纹。任何浮点不可行报告都须标清引擎与数值模式；争议边界优先使用独立算术／精确小模型复核。

## 7. 运行环境、依赖和许可

| 方案 | 官方发布事实 | 运行环境决策需要补齐的条件 |
| --- | --- | --- |
| OR-Tools | C++ 核心，官方安装入口含 Python、Java、C#；Apache-2.0。 | 目标 OS／CPU 架构上的发布包可用性、Python 等运行时、线程和内存。浏览器界面不等于可以直接在浏览器调用原生库。 |
| HiGHS | C++ 为主，官方仓库提供 Python 等接口和多平台构建；核心 MIT。当前二进制有 MIT 包及包含 HiPO 依赖的 Apache 包区分。 | 锁定 `highspy`／原生库组合；检查所用包的第三方 notices，不把核心许可等同于整包许可；验证是否暴露所需回调与多目标。 |
| SCIP | 8.0.3 起 Apache-2.0；官方提供原生包和 PySCIPOpt 入口；精确模式需 GMP、Boost multiprecision、MPFR、精确 LP solver 等。 | 普通模式与 exact build 分开标识；编译配置、可用插件、平台包和依赖许可按实际分发件核查。 |
| Gurobi | 官方支持平台文档及商业授权机制；pip／conda 的受限许可模型大小有限。 | 确认实际授权允许本用途、部署和并发；是否依赖网络取许可；目标 OS／架构和运行时。不能把示例成功当本规模可用许可。 |

来源：[OR-Tools 安装](https://developers.google.com/optimization/install)、[OR-Tools LICENSE](https://github.com/google/or-tools/blob/stable/LICENSE)、[HiGHS 官方仓库及分发说明](https://github.com/ERGO-Code/HiGHS)、[SCIP 发布与许可](https://www.scipopt.org/)、[SCIP exact 依赖](https://www.scipopt.org/doc-10.0.0/html/EXACT.php)、[Gurobi 平台](https://docs.gurobi.com/projects/optimizer/en/current/reference/releasenotes/platforms.html)、[Gurobi 受限许可说明](https://support.gurobi.com/hc/en-us/articles/360051597492-How-do-I-resolve-a-Model-too-large-for-size-limited-Gurobi-license-error)。

初次原型可在可复现的本地 worker 上比较，不因此决定最终部署。若要求纯浏览器，必须另测对应 Wasm 构建、线程／内存／取消和包维护；本报告不从原生官方支持推导浏览器支持。GPU 不是本次等待目标的先验要求。上述环境条件由 [单人使用的网页工具如何运行并保证计划数据可恢复？](https://github.com/Leegerger/auto_plan/issues/10) 决定；本研究不采购许可或选择服务器。

## 8. 可执行验证计划与判据

### 8.1 分三个证据层级，先正确性再性能

**A. 精确小 oracle。** 使用与求解模型独立的 Python `Fraction`／整数实现；枚举受限小实例中所有合法整数数量、允许的日／线分配与生产顺序，精确评价可行性及完整目标向量。先用一日容量／取整／CT 单元 oracle，再扩展 1–2 线、2–3 机种、2–3 Recipe、2 个需求周；每个 q 上界 0–3，并显式列出所有生产段组合和跨日切机分配。只在能证明枚举完整的实例上宣布全局最优。连续 H 可对固定离散模式解精确有理子问题，不用一个任意细网格冒充 oracle。复杂确认衔接须待契约明确。

**B. 小／中规模模型差分。** 同一输入用 CP-SAT 与 MIP，并与 A 的枚举答案比较；用正向／反向构造制造仅差 1 片、边界相等、月界、零工时、未知参数情况。双求解器互相一致不是独立真值，必须通过独立计算器。随机实例建议至少 100 个已知种子；固定反例全部通过后才进入性能调优。

**C. 真实与合成代表性负载。** 取得匿名实际快照并记录代表性；没有实际数据时仅报告合成负载表现，不能宣布业务验收。合成样本通过先造有效计划、再反推需求及随机扰动产生“已知可行见证”，并单独建立刻意硬冲突／无法全满足需求样本，避免把“不知道有无解”混成失败率。

### 8.2 随报告交付的最小可运行 oracle

将下面代码块保存为 `oracle.py`，运行 `python3 oracle.py`。只使用标准库。它验证两个精确 ceiling 关系、CT/Holiday 槽位规则、H 网格反例和一个**无跨周、切机耗时为零、一天两机种**的完整数量枚举。它不是完整排程实现，也不验证真实负载；后续原型必须按上节扩展覆盖。

```python
from fractions import Fraction as F
from datetime import date, timedelta
from itertools import product


def ceilq(x):
    return -((-x.numerator) // x.denominator)


def ceil_relation(n, b, y):
    return b * y - b + 1 <= n <= b * y


def output_slots(input_day, quantity, line_yield, model_yield, ct, holidays):
    # input values are exact Fraction; input_day is the production-day label.
    n = ct.numerator // ct.denominator
    frac = ct - n
    days = [input_day]
    d = input_day
    while len(days) <= n + 1:
        d += timedelta(days=1)
        if d not in holidays:
            days.append(d)
    slots = []
    for k, share in [(n, 1 - frac), (n + 1, frac)]:
        if share == 0:
            continue
        target = days[k]
        while target in holidays:
            target += timedelta(days=1)
        amount = ceilq(quantity * line_yield * model_yield * share)
        slots.append((target, amount))  # keep distinct even on same date
    return slots


def day_capacity_exists(qs, capacities, available, setup):
    # Exact elimination for this specific daily aggregate subproblem:
    # fixed daily setup use; no additional within-day timing constraints.
    assert all(q >= 0 for q in qs)
    if setup > available:
        return False
    active = [(q, c) for q, c in zip(qs, capacities) if q > 0]
    if not active:
        return True
    if any(c <= 0 for q, c in active):
        return False
    lower = sum((F(24 * (q - 1), c) for q, c in active), F(0))
    return lower < available - setup


for b in range(1, 17):
    for n in range(0, 81):
        expected = ceilq(F(n, b))
        matches = [y for y in range(82) if ceil_relation(n, b, y)]
        assert matches == [expected]

assert ceilq(F(1000 * 10, 24)) == 417
assert 2 * ceilq(F(1000 * 5, 24)) == 418  # forbidden per-split rounding
assert 2 * ceilq(F(931, 2)) == 932
assert 2 * ceilq(F(1002, 10)) == 202
assert ceilq(F(1000, 1) / F(931, 1000)) == 1075

holiday = {date(2026, 10, 10), date(2026, 10, 11)}
slots = output_slots(date(2026, 10, 10), 1000, F(95, 100),
                     F(98, 100), F(1, 2), holiday)
assert slots == [(date(2026, 10, 12), 466)] * 2

# Continuous H=0.025 hours each is a feasible witness.
assert ceilq(F(1000, 1) * F(1, 40) / 24) == 2
assert 2 * F(1, 40) + F(95, 100) == 1
assert day_capacity_exists((2, 2), (1000, 1000), F(1), F(95, 100))
grid_possible = any(
    ceilq(F(1000 * h1, 2400)) >= 2
    and ceilq(F(1000 * h2, 2400)) >= 2
    for h1 in range(6) for h2 in range(6) if h1 + h2 <= 5
)
assert not grid_possible
# Exact zero-time and exact lower-bound equality cannot make one extra piece.
assert not day_capacity_exists((1,), (1000,), F(0), F(0))
assert not day_capacity_exists((2,), (1000,), F(24, 1000), F(0))

# Complete tiny instance: 1 day/line, two different Recipes, each C=24;
# available 1h; switches cost 0h; unknown initial Recipe; CT=0, yield=1.
# Each model's same-week demand is 2; no baseline; no cross-week flow.
# q in 0..2 covers all demand-relevant production; surplus is forbidden.
# Any q=2 would require H>1h and is infeasible. Each q=1 needs H>0.
# Hence 1+1 is best despite having one switch: shortages dominate K.
scored = []
for q1, q2 in product(range(3), repeat=2):
    if day_capacity_exists((q1, q2), (24, 24), F(1), F(0)):
        shortage = 4 - q1 - q2
        switches = int(q1 > 0 and q2 > 0)
        vector = (shortage, shortage, switches, 0, 0)
        scored.append((vector, (q1, q2)))
best = min(vector for vector, q in scored)
assert best == (2, 2, 1, 0, 0)
assert {q for vector, q in scored if vector == best} == {(1, 1)}
# Lexicographic, not weighted totals: earlier shortage dominates later shortage.
assert (0, 500, 0) < (100, 0, 0)
print('Exact arithmetic and scoped tiny oracle checks passed.')
```

### 8.3 必须覆盖的固定回归样本

| 样本组 | 输入／扰动 | 必须断言的结果 |
| --- | --- | --- |
| 汇总舍入 | 同 Recipe 拆机种、拆记录、R1→R2→R1 回访；同机种跨线拆投入 | 不增加容量或产出舍入次数；日线 Recipe 与机种投入日各自汇总边界正确。 |
| 精度 | H=0、ceil 的整数边界及刚大于边界、0.025 反例、两位良率相乘、接近 int64 上界 | 结果与 Fraction 一致；溢出提前拒绝建模，不能回传伪可行／伪无解。 |
| CT／日历 | CT=0、0.5、1、1.5；Holiday 投入；两槽合并日期；月界／窗口尾 | 槽位分别取整；例外不改产出日历；尾部产出计迟交仅限有效确认。 |
| 日容量 | M+E>24、Holiday 例外、普通空闲提前切机、跨日切机 | T=max(24-M-E,0)；T=0 不能生产／切机；S 总额只付一次，空闲不重复抵扣。 |
| 状态／顺序 | 未知起点、同 Recipe 换机种、零时长换 Recipe、Holiday 保状态 | K 精确正确；不能把额外扣减=0 当 K=0；不伪造现场连续性结论。 |
| 确认批次 | 物料周二300＋周四200；出货周二100＋周五100；缩减、撤销、跨线分摊 | 物料总量500非800；第一出货批不能用到周五；状态和单位分清，额度不重复使用。 |
| 输入归属 | 同投入日跨两个需求周、双良率50%／CT0.5小量、名义投入与尾差 | 按后续确认的唯一语义通过；在决议前保留为待定 fixture，不能“挑一个方便答案”。 |
| 局部边界 | 仅改周三造成周四固定安排超容；隔多日普通空闲；零锁定和拆记录绕锁 | 不改范围外，能检测边界影响；数量锁不锁顺序；范围不足提示。 |
| 稳定性 | 近期移100片到另一日；远期同样移动；零基线／无基线／部分执行 | D 分别600／200；变化率分母与例外正确；超容忍度只提示。 |
| 目标排序 | 近期缺100 vs近期0远期缺500；同出货6次切机25%变化 vs12次5%；获准周五迟交 | 确认向量顺序；前者交期优先；6次优先；不私加“更早补足”层。 |
| 富余 | 需求减少／零需求；固定富余；舍入必要尾差 | 授权自由部分无无需求生产；固定部分保留提示；合法尾差不被错误删除。 |
| 状态／取消 | 有缺口可行；锁定硬冲突；强制零缺口无解；极短限时；取消无新解；崩溃／失效快照 | 各状态不混淆；保存已验证候选；拒绝直接采用过时结果；邻域无解不外推。 |

### 8.4 代表性性能负载

建议先冻结一份实验清单，再调参，避免只挑容易样本。每个系列至少 10 个不同种子／匿名快照，每个实例重复 5 次；记录全部失败，不删除异常值。CPU 线程设置固定，冷启动与热进程分开；单线程作可复现诊断，选定线程数作目标设备测试。具体样本数量可由验收议题调整，下列是初始技术实验方案，不冒充用户已批准 SLA。

| 系列 | 规模及因素 | 模式 |
| --- | --- | --- |
| 小 oracle | 1–2 线、2–3 机种、1–2 周，量0–3，完整枚举可承担 | 校验／证明，独立于30／120秒绩效统计。 |
| 中规模 | 4–6 线、10机种、4周，Recipe 5–10 | 全排／局部，用于定位模型尺寸与约束强弱。 |
| 目标规模 | 18线、40机种、105日；Recipe 10／20／40；覆盖多个月份 | 全排120秒目标；局部30秒目标。 |
| 负载／冲突 | 单独改变可生产稀疏度、70%／95%／110%名义负载、0%／30%／70%锁定、每对象0／1／5个确认批次 | 负载率仅是生成器参数，不能作为业务可行性证明。 |
| 编辑范围 | 1日1线、1周数线、4周、全窗口；需求±10%、Holiday变化、月度产能变化 | 候选保留边界；比较已可行草稿和需修复草稿。 |
| 难例 | 密集不对称切机、大量零耗时切换、多月良率跃迁、稀少可生产线、跨日提前切机、待确认／正式混合 | 分别统计失败原因，不能用平均值掩盖。 |

所有路线使用相同的业务快照、固定范围与独立验证器；先比较冷启动，再比较当前草稿 hints／starts，再比较同一构造基线的热启动。混合路线额外记录构造、修复、各邻域和验证耗时。调优集与保留评估集分开；若时间网格／段上限不同，属于不同可行域，不能直接比较成求解器速度胜负。

### 8.5 每次运行保存的数据

- 硬件：CPU 型号、架构、物理／逻辑核、分配线程、RAM、容器配额；OS／内核、运行时、是否省电／电池、其他负载。不要用执行本研究的机器自动代替目标用户设备。
- 软件：求解器与 wrapper 精确版本、build／commit、包哈希、许可证类型和取证方式、全部非默认参数；SCIP 是否 exact 及依赖版本；模型生成器／独立验证器提交哈希。
- 输入：快照哈希、样本来源、规模／密度、随机种子、修改范围、草稿可行性、边界及全部简化假设。
- 模型：原始／预处理后变量数、整数／布尔数、约束数、非零系数、顺序段／弧、系数最大幅度、峰值内存。
- 时钟：点击运行／收到请求、快照结束、建模结束、首次 solver incumbent、首次**验证通过可展示候选**、各阶段完成、总截止、最后校验与展示完成；取消请求／UI确认／worker停止／候选恢复分别记录。
- 结果：候选原始数据及 hash、33层向量、每周缺口／迟交／变化率、验证错误、每阶段 incumbent／bound／gap／状态／证明模式与认证前缀；模型及日志。没有 incumbent 时不得伪填0。

### 8.6 通过、失败及未得结论

**正确性硬门槛：** 全部已定固定样本、完整小 oracle、随机差分中，独立可行性与目标向量零不一致；不得有1片、1次切机、1个生产日或1份确认额度的容差妥协。范围外和执行事实零未经授权改变。发现一个伪有效候选、错误认证或错误“无解”，该路线当前模型即失败，先修复再计性能。未消歧 fixture 不算通过，也不算引擎能力失败，明确等待决议。

**延迟初始技术门槛：** 在冻结的“目标设备×代表性实例×重复运行”矩阵上，局部候选从请求到验证可展示≤30秒、全排≤120秒。分别报告 p50、p95、最大值、截止前得到有效候选比例及内存峰值；只有声明的矩阵中每次都满足，才说“该矩阵达标”，不能由 p95 或平均值宣称所有情况达标。未知可行性实例、已证明硬冲突、关键资料缺失单独统计状态反馈延迟，不伪装成候选成功。压力测试超过目标可以记录为失败边界，不能自动豁免后再把全部产品称为达标。

**取消初始技术门槛：** 在建模、预处理、无新 incumbent、优化后期、最后校验各时点注入取消；建议原型先测 UI 确认≤1秒、worker 停止或被监督隔离≤2秒且原子保存候选可恢复。该数值是待人类评议的技术目标，原业务仅说可随时停止，不能宣称这两秒已经获用户批准。未找到候选时也要停止；已有候选不得损坏。若引擎协作式取消超时，说明实际延迟和保全策略。

**质量门槛：** 小 oracle 完整模型必须给出同一最优目标向量，认证不能虚报；目标规模的有限时间结果必须通过硬约束、富余和确认检查，且不能在全向量上劣于同一预算下可用的已验证草稿／构造基线。记录与延长运行参考的“首次不同层、差多少片／次／代价”和认证前缀长度，不用一个百分比总分掩盖近期损失。若截止前只给出不劣基线但改善很少，技术可行性通过不代表业务质量已接受，由验收议题评议；未知全局最优的长跑答案仅叫参考，不能叫 oracle。

最终实验结论允许四种：明确通过某矩阵；明确失败及反例；实现／版本能力不支持；证据不足需更多数据或时间。官方功能存在不自动升级为“通过”。

## 9. 下一步决策与交接

研究已回答“有哪些可候选路线、能表达哪些组件、哪里不能无条件精确、如何验证”。不应在本票直接锁定算法或部署。

建议从研究结果毕业为两个紧凑的业务澄清议题和一个技术原型议题：

1. **Recipe 分配工时应采用什么精度，如何保持容量向上取整的精确含义？** 用 0.025 小时反例决定是继续允许任意有理／连续 H 并证明转换，还是用户明确选择某一精度；不能由算法偏好代答。
2. **需求对应投入、分批确认与汇总产出尾差如何保持一致？** 只补齐跨需求周归属和名义所需投入与真实逐笔产出的衔接，不改已定总产出／FIFO／确认边界。用上述小量案例和跨月案例明确正式可行性。
3. **哪条求解路线能通过规则校验和 30 秒／2 分钟验证？** 在前两项明确后完成可丢弃技术原型，至少比较 CP-SAT、一个开源 MIP 和混合启动；条件允许再加入 Gurobi；将实验报告交给人类评议并据证据选路线。最终设备性能结论依赖运行环境决定，但算术 oracle 和无歧义小模型不必等部署结束。

[怎样判定三种排程方式已满足业务验收？](https://github.com/Leegerger/auto_plan/issues/11) 继续负责真实样本、验收组织与业务质量接受；技术原型提供证据，不与它互设循环阻塞。每周重置的投入差额分配、数据迁移和页面交互仍在各自议题处理，不并入算法研究。

研究执行记录：已运行本文标准库小 oracle，全部断言通过；独立核对另用 Fraction 检验 22,500 组 ceiling 等价关系及相邻错误值，并核对工时网格、1072／1075 投入和词典序反例。唯一实测层级为精确算术与限定小实例枚举；**未安装、实现或运行任何生产级求解器模型，未进行 18×40×105 性能实验，未决定最终部署与商业许可。**
