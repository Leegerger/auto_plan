import os
import sys
import time
import json
import uuid
import datetime
import threading
import statistics
import oracledb

oracledb.init_oracle_client(lib_dir='/opt/oracle/instantclient_21_13')

DSN = os.getenv('ORACLE_DSN', '10.0.0.2:1521/FSSCM')
USER = os.getenv('ORACLE_USER', 'PLAN_TEST')
PASSWORD = os.getenv('ORACLE_PASSWORD', 'PLAN_TEST2026')

def get_connection():
    return oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)

def get_pool(min_pool=1, max_pool=10, increment=1):
    return oracledb.create_pool(
        user=USER,
        password=PASSWORD,
        dsn=DSN,
        min=min_pool,
        max=max_pool,
        increment=increment
    )

def setup_schema(conn):
    with conn.cursor() as cur:
        tables = [
            'PROTO_PLAN_AUDIT',
            'PROTO_PLAN_ADOPTION_ROW',
            'PROTO_PLAN_ADOPTION',
            'PROTO_PLAN_SCOPE_KEY',
            'PROTO_PLAN_DRAFT_REVISION',
            'PROTO_PLAN_IDEMPOTENCY',
            'PROTO_PLAN_HEADER'
        ]
        for t in tables:
            try:
                cur.execute(f"DROP TABLE {t} CASCADE CONSTRAINTS PURGE")
            except Exception:
                pass
        
        # 1. 计划头行（控制单机排程头锁与全局修订/采用版指针）
        cur.execute("""
        CREATE TABLE PROTO_PLAN_HEADER (
            PLAN_ID VARCHAR2(32) PRIMARY KEY,
            CURRENT_REVISION NUMBER(10) NOT NULL,
            CURRENT_ADOPTION_NO NUMBER(10) NOT NULL,
            UPDATED_AT TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
        )
        """)
        cur.execute("""
        INSERT INTO PROTO_PLAN_HEADER (PLAN_ID, CURRENT_REVISION, CURRENT_ADOPTION_NO)
        VALUES ('BONDING_MAIN', 1, 1)
        """)

        # 2. 幂等键表（状态：COMMITTED, ABORTED_VOID, IN_PROGRESS）
        cur.execute("""
        CREATE TABLE PROTO_PLAN_IDEMPOTENCY (
            IDEMPOTENCY_KEY VARCHAR2(64) PRIMARY KEY,
            STATUS VARCHAR2(20) NOT NULL,
            OP_TYPE VARCHAR2(20) NOT NULL,
            RESULT_REVISION NUMBER(10),
            RESULT_ADOPTION_NO NUMBER(10),
            RESPONSE_PAYLOAD CLOB,
            CREATED_AT TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
            UPDATED_AT TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
        )
        """)

        # 3. 共享草稿修订表
        cur.execute("""
        CREATE TABLE PROTO_PLAN_DRAFT_REVISION (
            REVISION NUMBER(10) PRIMARY KEY,
            PARENT_REVISION NUMBER(10) NOT NULL,
            AUTHOR VARCHAR2(64) NOT NULL,
            SUMMARY VARCHAR2(200),
            CREATED_AT TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
        )
        """)
        cur.execute("""
        INSERT INTO PROTO_PLAN_DRAFT_REVISION (REVISION, PARENT_REVISION, AUTHOR, SUMMARY)
        VALUES (1, 0, 'SYSTEM', 'Initial baseline revision')
        """)

        # 4. 修订影响范围键表（线体日容量键 CAP:LINE:DATE、需求确认键 REQ:MODEL:WEEK、单元格键 CELL:DATE:LINE:MODEL）
        cur.execute("""
        CREATE TABLE PROTO_PLAN_SCOPE_KEY (
            REVISION NUMBER(10) NOT NULL,
            SCOPE_KEY VARCHAR2(100) NOT NULL,
            CONSTRAINT PK_PROTO_SCOPE PRIMARY KEY (REVISION, SCOPE_KEY)
        )
        """)

        # 5. 采用版表（只允许插入，禁止 UPDATE/DELETE）
        cur.execute("""
        CREATE TABLE PROTO_PLAN_ADOPTION (
            ADOPTION_NO NUMBER(10) PRIMARY KEY,
            BASE_REVISION NUMBER(10) NOT NULL,
            ADOPTED_BY VARCHAR2(64) NOT NULL,
            SNAPSHOT_META CLOB NOT NULL,
            ADOPTED_AT TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
        )
        """)
        cur.execute("""
        INSERT INTO PROTO_PLAN_ADOPTION (ADOPTION_NO, BASE_REVISION, ADOPTED_BY, SNAPSHOT_META)
        VALUES (1, 1, 'SYSTEM', 'Initial baseline adoption')
        """)

        # 6. 采用版关系行明细表（计划明细：日期×线体×机种×数量）
        cur.execute("""
        CREATE TABLE PROTO_PLAN_ADOPTION_ROW (
            ADOPTION_NO NUMBER(10) NOT NULL,
            LINE_ID VARCHAR2(32) NOT NULL,
            PROD_DATE VARCHAR2(10) NOT NULL,
            MODEL_ID VARCHAR2(32) NOT NULL,
            QUANTITY NUMBER(10) NOT NULL,
            CONSTRAINT PK_PROTO_ADOPT_ROW PRIMARY KEY (ADOPTION_NO, LINE_ID, PROD_DATE, MODEL_ID)
        )
        """)

        # 7. 审计表（只允许插入，禁止 UPDATE/DELETE）
        cur.execute("""
        CREATE TABLE PROTO_PLAN_AUDIT (
            AUDIT_ID NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            ACTION_TYPE VARCHAR2(32) NOT NULL,
            OPERATOR VARCHAR2(64) NOT NULL,
            DETAIL CLOB NOT NULL,
            LOGGED_AT TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
        )
        """)

        # 8. 触发器：防篡改触发器，拒绝对采用版与审计表的 UPDATE/DELETE
        cur.execute("""
        CREATE OR REPLACE TRIGGER TRG_ADOPT_NO_MUTATION
        BEFORE UPDATE OR DELETE ON PROTO_PLAN_ADOPTION
        FOR EACH ROW
        BEGIN
            RAISE_APPLICATION_ERROR(-20001, 'IMMUTABLE_ERROR: PROTO_PLAN_ADOPTION is append-only. UPDATE/DELETE prohibited.');
        END;
        """)

        cur.execute("""
        CREATE OR REPLACE TRIGGER TRG_ADOPT_ROW_NO_MUTATION
        BEFORE UPDATE OR DELETE ON PROTO_PLAN_ADOPTION_ROW
        FOR EACH ROW
        BEGIN
            RAISE_APPLICATION_ERROR(-20002, 'IMMUTABLE_ERROR: PROTO_PLAN_ADOPTION_ROW is append-only. UPDATE/DELETE prohibited.');
        END;
        """)

        cur.execute("""
        CREATE OR REPLACE TRIGGER TRG_AUDIT_NO_MUTATION
        BEFORE UPDATE OR DELETE ON PROTO_PLAN_AUDIT
        FOR EACH ROW
        BEGIN
            RAISE_APPLICATION_ERROR(-20003, 'IMMUTABLE_ERROR: PROTO_PLAN_AUDIT is append-only. UPDATE/DELETE prohibited.');
        END;
        """)

        conn.commit()
        print("Schema setup completed successfully.")


# =========================================================================
# 验证项 1: 并发采用竞争与锁等待上限
# =========================================================================
def test_item_1_concurrent_adoption(pool):
    print("\n--- [Test 1] 两个并发事务同时采用：头行锁排他性与等待上限到期行为 ---")
    results = {}
    
    # 场景 A: 两个并发事务同时尝试采用同一基准版本 (base_rev=1, base_adopt=1)
    # T1 先获取锁并写入，T2 在锁释放后读取到最新采用版已改变，主动拒绝并回滚
    # 场景 B: T1 持有锁不释放，T2 等待上限（NOWAIT 或 WAIT 2秒）到期后的错误行为
    
    # 测试 B: WAIT 2秒到期异常行为实测
    barrier = threading.Barrier(2)
    
    def holder_worker():
        conn = pool.acquire()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT CURRENT_ADOPTION_NO FROM PROTO_PLAN_HEADER WHERE PLAN_ID = 'BONDING_MAIN' FOR UPDATE")
                cur.fetchone()
                barrier.wait()
                # 持续持有锁 4 秒
                time.sleep(4)
            conn.rollback()
        finally:
            pool.release(conn)

    def waiter_worker():
        barrier.wait()
        time.sleep(0.1) # 确保 holder 已进入锁
        conn = pool.acquire()
        t0 = time.time()
        err_code = None
        err_msg = ""
        try:
            with conn.cursor() as cur:
                # Oracle 等待上限实测 (WAIT 2 秒)
                cur.execute("SELECT CURRENT_ADOPTION_NO FROM PROTO_PLAN_HEADER WHERE PLAN_ID = 'BONDING_MAIN' FOR UPDATE WAIT 2")
                cur.fetchone()
        except oracledb.DatabaseError as e:
            err = e.args[0]
            err_code = err.code
            err_msg = err.message
        finally:
            elapsed = time.time() - t0
            pool.release(conn)
            results['wait_timeout'] = {
                'elapsed': round(elapsed, 2),
                'err_code': err_code,
                'err_msg': err_msg
            }

    t_holder = threading.Thread(target=holder_worker)
    t_waiter = threading.Thread(target=waiter_worker)
    t_holder.start()
    t_waiter.start()
    t_holder.join()
    t_waiter.join()

    print(f"  [1.B 等待上限实测] WAIT 2秒到期捕获异常: ORA-{results['wait_timeout']['err_code']}, 耗时: {results['wait_timeout']['elapsed']}s")
    print(f"       错误消息: {results['wait_timeout']['err_msg'].strip()}")
    assert results['wait_timeout']['err_code'] == 30006, f"Expected ORA-30006 (resource busy; acquire with WAIT timeout expired), got {results['wait_timeout']['err_code']}"

    # 测试 A: 两个并发采用事务（真实采用逻辑，WAIT 10秒）
    # User A 和 User B 同时基于 base_adoption_no=1 发起采用
    barrier_adopt = threading.Barrier(2)
    adopt_results = {}

    def adopt_tx(user_name, delay_hold):
        conn = pool.acquire()
        tx_res = {}
        try:
            with conn.cursor() as cur:
                barrier_adopt.wait()
                t_start = time.time()
                # 1. 锁头行，等待上限 10 秒
                cur.execute("SELECT CURRENT_REVISION, CURRENT_ADOPTION_NO FROM PROTO_PLAN_HEADER WHERE PLAN_ID = 'BONDING_MAIN' FOR UPDATE WAIT 10")
                row = cur.fetchone()
                cur_rev, cur_adopt = row[0], row[1]
                
                # 检查基准采用版是否已变（假设用户发起时都是基准 adopt_no=1）
                if cur_adopt != 1:
                    conn.rollback()
                    tx_res = {
                        'status': 'REJECTED_STALE_ADOPTION',
                        'seen_adoption_no': cur_adopt,
                        'elapsed': round(time.time() - t_start, 2)
                    }
                else:
                    # 模拟校验与耗时操作
                    time.sleep(delay_hold)
                    new_adopt = cur_adopt + 1
                    # 插入采用版
                    cur.execute("""
                        INSERT INTO PROTO_PLAN_ADOPTION (ADOPTION_NO, BASE_REVISION, ADOPTED_BY, SNAPSHOT_META)
                        VALUES (:1, :2, :3, :4)
                    """, (new_adopt, cur_rev, user_name, f"Adoption by {user_name}"))
                    
                    # 插入幂等键
                    idem_key = f"IDEM_ADOPT_{user_name}_{new_adopt}"
                    cur.execute("""
                        INSERT INTO PROTO_PLAN_IDEMPOTENCY (IDEMPOTENCY_KEY, STATUS, OP_TYPE, RESULT_ADOPTION_NO)
                        VALUES (:1, 'COMMITTED', 'ADOPTION', :2)
                    """, (idem_key, new_adopt))

                    # 插入审计记录
                    cur.execute("""
                        INSERT INTO PROTO_PLAN_AUDIT (ACTION_TYPE, OPERATOR, DETAIL)
                        VALUES ('ADOPT_PLAN', :1, :2)
                    """, (user_name, f"Adopted version {new_adopt}"))

                    # 更新头行
                    cur.execute("""
                        UPDATE PROTO_PLAN_HEADER 
                        SET CURRENT_ADOPTION_NO = :1, UPDATED_AT = SYSTIMESTAMP 
                        WHERE PLAN_ID = 'BONDING_MAIN'
                    """, (new_adopt,))
                    
                    conn.commit()
                    tx_res = {
                        'status': 'SUCCESS_ADOPTED',
                        'adopted_version': new_adopt,
                        'elapsed': round(time.time() - t_start, 2)
                    }
        except Exception as e:
            conn.rollback()
            tx_res = {'status': 'ERROR', 'error': str(e)}
        finally:
            pool.release(conn)
            adopt_results[user_name] = tx_res

    t_u1 = threading.Thread(target=adopt_tx, args=('USER_ALICE', 1.0))
    t_u2 = threading.Thread(target=adopt_tx, args=('USER_BOB', 0.5))
    t_u1.start()
    t_u2.start()
    t_u1.join()
    t_u2.join()

    print(f"  [1.A 并发采用竞争结果] ALICE: {adopt_results['USER_ALICE']}")
    print(f"                       BOB:   {adopt_results['USER_BOB']}")
    statuses = {adopt_results['USER_ALICE']['status'], adopt_results['USER_BOB']['status']}
    assert 'SUCCESS_ADOPTED' in statuses and 'REJECTED_STALE_ADOPTION' in statuses, "One transaction must succeed and the other must see changed adoption_no and be rejected!"

    # 验证数据库最终状态：采用版严格只增加 1 个（变为 2），不会出现并发重叠采用
    conn = pool.acquire()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT CURRENT_ADOPTION_NO FROM PROTO_PLAN_HEADER WHERE PLAN_ID = 'BONDING_MAIN'")
            final_adopt = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM PROTO_PLAN_ADOPTION")
            count_adopt = cur.fetchone()[0]
            print(f"  [1.A 状态复核] Header CURRENT_ADOPTION_NO = {final_adopt}, Total Adoption Records = {count_adopt}")
            assert final_adopt == 2 and count_adopt == 2
    finally:
        pool.release(conn)

    return {'wait_timeout': results['wait_timeout'], 'concurrent_adoption': adopt_results}


# =========================================================================
# 验证项 2: 同一幂等键重复提交
# =========================================================================
def test_item_2_idempotency_replay(pool):
    print("\n--- [Test 2] 同一幂等键重复提交：返回第一次结果，不产生第二个修订或采用版 ---")
    
    idem_key = "IDEM_REQ_SAVE_TEST_001"
    author = "USER_CHARLIE"
    
    def execute_save_operation(key, base_rev, summary):
        conn = pool.acquire()
        try:
            with conn.cursor() as cur:
                # 检查幂等键表是否已存在
                cur.execute("SELECT STATUS, RESULT_REVISION, RESPONSE_PAYLOAD FROM PROTO_PLAN_IDEMPOTENCY WHERE IDEMPOTENCY_KEY = :1", (key,))
                existing = cur.fetchone()
                if existing:
                    status, res_rev, payload = existing
                    payload_str = payload.read() if hasattr(payload, 'read') else payload
                    return {
                        'idempotent_replayed': True,
                        'status': status,
                        'result_revision': res_rev,
                        'payload': payload_str
                    }
                
                # 事务开始：锁头行
                cur.execute("SELECT CURRENT_REVISION FROM PROTO_PLAN_HEADER WHERE PLAN_ID = 'BONDING_MAIN' FOR UPDATE WAIT 10")
                cur_rev = cur.fetchone()[0]
                
                new_rev = cur_rev + 1
                response_data = json.dumps({'msg': 'Saved successfully', 'revision': new_rev, 'author': author})
                
                # 插入幂等键 (状态 COMMITTED)
                cur.execute("""
                    INSERT INTO PROTO_PLAN_IDEMPOTENCY (IDEMPOTENCY_KEY, STATUS, OP_TYPE, RESULT_REVISION, RESPONSE_PAYLOAD)
                    VALUES (:1, 'COMMITTED', 'SAVE_DRAFT', :2, :3)
                """, (key, new_rev, response_data))
                
                # 插入修订
                cur.execute("""
                    INSERT INTO PROTO_PLAN_DRAFT_REVISION (REVISION, PARENT_REVISION, AUTHOR, SUMMARY)
                    VALUES (:1, :2, :3, :4)
                """, (new_rev, cur_rev, author, summary))
                
                # 更新头行
                cur.execute("""
                    UPDATE PROTO_PLAN_HEADER
                    SET CURRENT_REVISION = :1, UPDATED_AT = SYSTIMESTAMP
                    WHERE PLAN_ID = 'BONDING_MAIN'
                """, (new_rev,))
                
                conn.commit()
                return {
                    'idempotent_replayed': False,
                    'status': 'COMMITTED',
                    'result_revision': new_rev,
                    'payload': response_data
                }
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            pool.release(conn)

    # 第一次提交
    res1 = execute_save_operation(idem_key, 1, "First save with idempotency key")
    print(f"  第一次保存结果: replayed={res1['idempotent_replayed']}, rev={res1['result_revision']}, status={res1['status']}")
    assert not res1['idempotent_replayed']
    rev_after_first = res1['result_revision']

    # 第二次提交（带相同幂等键）
    res2 = execute_save_operation(idem_key, 1, "Duplicate save with same idempotency key")
    print(f"  第二次重复提交: replayed={res2['idempotent_replayed']}, rev={res2['result_revision']}, status={res2['status']}")
    assert res2['idempotent_replayed']
    assert res2['result_revision'] == rev_after_first
    assert res2['payload'] == res1['payload']

    # 验证数据库中修订号和头行状态没有多增
    conn = pool.acquire()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT CURRENT_REVISION FROM PROTO_PLAN_HEADER WHERE PLAN_ID = 'BONDING_MAIN'")
            header_rev = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM PROTO_PLAN_DRAFT_REVISION WHERE REVISION = :1", (rev_after_first,))
            rev_count = cur.fetchone()[0]
            print(f"  状态验证: Header CURRENT_REVISION = {header_rev}, Revision {rev_after_first} count = {rev_count}")
            assert header_rev == rev_after_first and rev_count == 1
    finally:
        pool.release(conn)

    return {'first_call': res1, 'duplicate_call': res2}


# =========================================================================
# 验证项 3: 请求在途中连接断开与核实接口原子“作废”键机制
# =========================================================================
def test_item_3_network_disconnect_and_verification_void(pool):
    print("\n--- [Test 3] 途中断开连接：核实接口原子写入“作废”键，迟到旧请求因唯一约束失败 ---")

    test_key = "IDEM_DISCONNECTED_REQ_999"
    
    # 模拟核实接口：客户端掉线重连后，不知道之前的保存请求到底有没有执行
    # 核实接口逻辑：
    # 查询 IDEMPOTENCY_KEY：
    # 如果已存在且 STATUS == 'COMMITTED'，说明在断开前已成功提交，返回'COMMITTED'；
    # 如果已存在且 STATUS == 'ABORTED_VOID'，说明之前已被核实作废，返回'ABORTED_VOID'；
    # 如果不存在，则原子插入一条 STATUS='ABORTED_VOID' 记录并 COMMIT，抢占该键，返回'ABORTED_VOID'。
    
    def verify_status_or_void(key):
        conn = pool.acquire()
        try:
            with conn.cursor() as cur:
                # 尝试查询
                cur.execute("SELECT STATUS, RESULT_REVISION FROM PROTO_PLAN_IDEMPOTENCY WHERE IDEMPOTENCY_KEY = :1", (key,))
                row = cur.fetchone()
                if row:
                    return {'action': 'FOUND_EXISTING', 'status': row[0], 'result_revision': row[1]}
                
                # 键不存在：原子插入 ABORTED_VOID
                try:
                    cur.execute("""
                        INSERT INTO PROTO_PLAN_IDEMPOTENCY (IDEMPOTENCY_KEY, STATUS, OP_TYPE, RESPONSE_PAYLOAD)
                        VALUES (:1, 'ABORTED_VOID', 'UNKNOWN_VERIFY', 'Marked void by verification endpoint')
                    """, (key,))
                    conn.commit()
                    return {'action': 'INSERTED_VOID', 'status': 'ABORTED_VOID', 'result_revision': None}
                except oracledb.IntegrityError:
                    # 并发下恰好有请求刚刚提交了
                    conn.rollback()
                    cur.execute("SELECT STATUS, RESULT_REVISION FROM PROTO_PLAN_IDEMPOTENCY WHERE IDEMPOTENCY_KEY = :1", (key,))
                    r = cur.fetchone()
                    return {'action': 'RACE_FOUND', 'status': r[0], 'result_revision': r[1]}
        finally:
            pool.release(conn)

    # 1. 模拟网络丢包：旧请求尚未进入数据库事务或还在网络途中
    # 客户端重连调用核实接口：
    verify_res1 = verify_status_or_void(test_key)
    print(f"  [核实调用] 键不存在，核实接口原子抢占作废: {verify_res1}")
    assert verify_res1['status'] == 'ABORTED_VOID'

    # 2. 迟到的旧请求现在终于到达数据库，尝试执行原定的保存事务
    # 保存事务逻辑：
    # 事务内首先 INSERT INTO PROTO_PLAN_IDEMPOTENCY (IDEMPOTENCY_KEY, STATUS='COMMITTED')
    # 此时必然触发 ORA-00001 (唯一约束违反)，整个事务立即 ROLLBACK！
    conn = pool.acquire()
    stale_req_result = {}
    try:
        with conn.cursor() as cur:
            # 锁定头行
            cur.execute("SELECT CURRENT_REVISION FROM PROTO_PLAN_HEADER WHERE PLAN_ID = 'BONDING_MAIN' FOR UPDATE WAIT 5")
            cur_rev = cur.fetchone()[0]

            # 插入幂等键
            try:
                cur.execute("""
                    INSERT INTO PROTO_PLAN_IDEMPOTENCY (IDEMPOTENCY_KEY, STATUS, OP_TYPE, RESULT_REVISION)
                    VALUES (:1, 'COMMITTED', 'SAVE_DRAFT', :2)
                """, (test_key, cur_rev + 1))
                # 如果插入成功（不应该成功），执行保存逻辑
                cur.execute("""
                    INSERT INTO PROTO_PLAN_DRAFT_REVISION (REVISION, PARENT_REVISION, AUTHOR, SUMMARY)
                    VALUES (:1, :2, 'STALE_USER', 'Stale write that should fail')
                """, (cur_rev + 1, cur_rev))
                conn.commit()
                stale_req_result['status'] = 'UNEXPECTED_SUCCESS'
            except oracledb.IntegrityError as e:
                conn.rollback()
                err = e.args[0]
                stale_req_result = {
                    'status': 'FAILED_UNIQUE_CONSTRAINT',
                    'err_code': err.code,
                    'err_msg': err.message.strip()
                }
    finally:
        pool.release(conn)

    print(f"  [迟到旧请求行为] 尝试写入被拦截: {stale_req_result}")
    assert stale_req_result['status'] == 'FAILED_UNIQUE_CONSTRAINT'
    assert stale_req_result['err_code'] == 1, f"Expected ORA-00001 unique constraint violated, got {stale_req_result['err_code']}"

    # 3. 再次调用核实接口，结果必须依然稳定是 ABORTED_VOID，绝无部分写入
    verify_res2 = verify_status_or_void(test_key)
    print(f"  [再次核实] 结果状态必须依然是作废: {verify_res2}")
    assert verify_res2['status'] == 'ABORTED_VOID'

    # 4. 验证 PROTO_PLAN_DRAFT_REVISION 中没有产生脏数据
    conn = pool.acquire()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM PROTO_PLAN_DRAFT_REVISION WHERE AUTHOR = 'STALE_USER'")
            stale_count = cur.fetchone()[0]
            print(f"  [数据纯净性复核] 迟到请求脏数据记录数: {stale_count} (必须为0)")
            assert stale_count == 0
    finally:
        pool.release(conn)

    return {'verify_void': verify_res1, 'stale_request': stale_req_result, 'verify_recheck': verify_res2}


# =========================================================================
# 验证项 4: 影响范围键交集判定（不交叉分别保存，同处冲突返回差异，批量操作按并集）
# =========================================================================
def test_item_4_scope_keys_conflict(pool):
    print("\n--- [Test 4] 影响范围键交集判定：独立范围并发分别成功，同处冲突返回 409 与差异 ---")

    # 业务范围键类型定义 (来自议题 18 决议):
    # 1. CAP:<LINE>:<DATE> (线体日容量键)
    # 2. REQ:<MODEL>:<WEEK> (需求与确认键)
    # 3. CELL:<DATE>:<LINE>:<MODEL> (单元格键)

    # 编写一个基于头行锁与范围键求交的保存函数
    def try_save_with_scope(author, base_rev, scope_keys, summary):
        conn = pool.acquire()
        try:
            with conn.cursor() as cur:
                # 1. 事务内锁头行
                cur.execute("SELECT CURRENT_REVISION FROM PROTO_PLAN_HEADER WHERE PLAN_ID = 'BONDING_MAIN' FOR UPDATE WAIT 10")
                latest_rev = cur.fetchone()[0]

                # 2. 如果基准版本落后 (base_rev < latest_rev)，查询从 (base_rev + 1) 到 latest_rev 的所有已提交范围键
                if base_rev < latest_rev:
                    cur.execute("""
                        SELECT DISTINCT SCOPE_KEY
                        FROM PROTO_PLAN_SCOPE_KEY
                        WHERE REVISION > :1 AND REVISION <= :2
                    """, (base_rev, latest_rev))
                    committed_keys = {row[0] for row in cur.fetchall()}
                    
                    # 求交集
                    intersection = committed_keys.intersection(set(scope_keys))
                    if intersection:
                        # 发现冲突：回滚事务，返回 409 冲突及差异键
                        conn.rollback()
                        return {
                            'success': False,
                            'status_code': 409,
                            'conflict_keys': sorted(list(intersection)),
                            'base_rev': base_rev,
                            'latest_rev': latest_rev
                        }
                
                # 3. 无交集冲突：允许写入并推进修订号
                new_rev = latest_rev + 1
                cur.execute("""
                    INSERT INTO PROTO_PLAN_DRAFT_REVISION (REVISION, PARENT_REVISION, AUTHOR, SUMMARY)
                    VALUES (:1, :2, :3, :4)
                """, (new_rev, base_rev, author, summary))

                # 写入该修订的影响范围键
                scope_data = [(new_rev, k) for k in scope_keys]
                cur.executemany("INSERT INTO PROTO_PLAN_SCOPE_KEY (REVISION, SCOPE_KEY) VALUES (:1, :2)", scope_data)

                # 更新头行当前修订号
                cur.execute("""
                    UPDATE PROTO_PLAN_HEADER
                    SET CURRENT_REVISION = :1, UPDATED_AT = SYSTIMESTAMP
                    WHERE PLAN_ID = 'BONDING_MAIN'
                """, (new_rev,))

                conn.commit()
                return {
                    'success': True,
                    'status_code': 200,
                    'new_revision': new_rev,
                    'base_rev': base_rev
                }
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            pool.release(conn)

    # 准备起点：先读取当前的最新修订号
    conn = pool.acquire()
    with conn.cursor() as cur:
        cur.execute("SELECT CURRENT_REVISION FROM PROTO_PLAN_HEADER WHERE PLAN_ID = 'BONDING_MAIN'")
        base_rev_0 = cur.fetchone()[0]
    pool.release(conn)

    # 场景 1: 用户 A 编辑 Line 1 (2026-10-15)，机种 M1
    scope_user_a = [
        "CAP:LINE_1:2026-10-15",
        "CELL:2026-10-15:LINE_1:M1",
        "REQ:M1:W202642"
    ]
    res_a = try_save_with_scope("USER_A", base_rev_0, scope_user_a, "User A edits Line 1 on 10-15")
    print(f"  [4.1 用户 A 保存 Line 1] 结果: {res_a}")
    assert res_a['success'] and res_a['status_code'] == 200
    rev_after_a = res_a['new_revision']

    # 场景 2: 用户 B 在同一基准版本 base_rev_0 下，编辑完全不交叉的位置：Line 2 (2026-10-16)，机种 M2
    # 虽然用户 B 的基准版本落后了（base_rev_0 < rev_after_a），但范围完全不重叠！
    scope_user_b = [
        "CAP:LINE_2:2026-10-16",
        "CELL:2026-10-16:LINE_2:M2",
        "REQ:M2:W202642"
    ]
    res_b = try_save_with_scope("USER_B", base_rev_0, scope_user_b, "User B edits Line 2 on 10-16 (Disjoint)")
    print(f"  [4.2 用户 B 不交叉范围保存] 基准落后但无交集，结果: {res_b}")
    assert res_b['success'] and res_b['status_code'] == 200, "Disjoint changes must succeed even if base_revision is behind!"
    rev_after_b = res_b['new_revision']

    # 场景 3: 用户 C 同样从基准 base_rev_0 发起，但修改了 Line 1 (2026-10-15)，与用户 A 发生冲突
    scope_user_c = [
        "CAP:LINE_1:2026-10-15",
        "CELL:2026-10-15:LINE_1:M1",
        "REQ:M1:W202642"
    ]
    res_c = try_save_with_scope("USER_C", base_rev_0, scope_user_c, "User C edits Line 1 (Conflicting)")
    print(f"  [4.3 用户 C 同处冲突保存] 结果: {res_c}")
    assert not res_c['success'] and res_c['status_code'] == 409
    print(f"       捕获冲突键: {res_c['conflict_keys']}")
    assert "CAP:LINE_1:2026-10-15" in res_c['conflict_keys']
    assert "CELL:2026-10-15:LINE_1:M1" in res_c['conflict_keys']

    # 场景 4: 批量操作（批量追加/交换/跨日顺延）—— 展开为完整并集键
    # 批量交换 Line 1 与 Line 2 在 10-15 的排程：
    # 影响范围必须涵盖涉及的两条线体容量键、机种需求键及各自涉及的单元格
    batch_scope = [
        "CAP:LINE_1:2026-10-15",
        "CAP:LINE_2:2026-10-15",
        "CELL:2026-10-15:LINE_1:M1",
        "CELL:2026-10-15:LINE_2:M2",
        "REQ:M1:W202642",
        "REQ:M2:W202642"
    ]
    # 如果基于旧基准提交该批量操作，因为涵盖了已修改的 Line 1，必然按整体判定冲突！
    res_batch_stale = try_save_with_scope("USER_BATCH", base_rev_0, batch_scope, "Batch swap based on stale rev")
    print(f"  [4.4 批量操作旧基准冲突判定] 结果: {res_batch_stale}")
    assert not res_batch_stale['success'] and res_batch_stale['status_code'] == 409

    # 如果基于最新版本 rev_after_b 提交批量操作，则成功写入
    res_batch_fresh = try_save_with_scope("USER_BATCH", rev_after_b, batch_scope, "Batch swap based on fresh rev")
    print(f"  [4.5 批量操作最新基准提交] 结果: {res_batch_fresh}")
    assert res_batch_fresh['success'] and res_batch_fresh['status_code'] == 200

    return {
        'single_disjoint': res_b,
        'single_conflict': res_c,
        'batch_conflict': res_batch_stale,
        'batch_success': res_batch_fresh
    }


# =========================================================================
# 验证项 5: 触发器防篡改 (拒绝对采用版与审计表的 UPDATE/DELETE) 与单个采用版体积估算
# =========================================================================
def test_item_5_trigger_immutability_and_volume(pool):
    print("\n--- [Test 5] 触发器防篡改与单采用版（快照+关系行）体积估算 ---")

    conn = pool.acquire()
    trigger_results = {}
    try:
        with conn.cursor() as cur:
            # 1. 尝试 UPDATE PROTO_PLAN_ADOPTION
            try:
                cur.execute("UPDATE PROTO_PLAN_ADOPTION SET ADOPTED_BY = 'HACKER' WHERE ADOPTION_NO = 1")
                trigger_results['update_adoption'] = 'UNEXPECTED_SUCCESS'
            except oracledb.DatabaseError as e:
                err = e.args[0]
                trigger_results['update_adoption'] = {'code': err.code, 'msg': err.message.strip()}

            # 2. 尝试 DELETE PROTO_PLAN_ADOPTION
            try:
                cur.execute("DELETE FROM PROTO_PLAN_ADOPTION WHERE ADOPTION_NO = 1")
                trigger_results['delete_adoption'] = 'UNEXPECTED_SUCCESS'
            except oracledb.DatabaseError as e:
                err = e.args[0]
                trigger_results['delete_adoption'] = {'code': err.code, 'msg': err.message.strip()}

            # 3. 尝试 UPDATE PROTO_PLAN_AUDIT
            # 先插一条审计
            cur.execute("INSERT INTO PROTO_PLAN_AUDIT (ACTION_TYPE, OPERATOR, DETAIL) VALUES ('TEST', 'ADMIN', 'Initial log')")
            conn.commit()
            try:
                cur.execute("UPDATE PROTO_PLAN_AUDIT SET OPERATOR = 'TAMPERED'")
                trigger_results['update_audit'] = 'UNEXPECTED_SUCCESS'
            except oracledb.DatabaseError as e:
                err = e.args[0]
                trigger_results['update_audit'] = {'code': err.code, 'msg': err.message.strip()}

            # 4. 尝试 DELETE PROTO_PLAN_AUDIT
            try:
                cur.execute("DELETE FROM PROTO_PLAN_AUDIT")
                trigger_results['delete_audit'] = 'UNEXPECTED_SUCCESS'
            except oracledb.DatabaseError as e:
                err = e.args[0]
                trigger_results['delete_audit'] = {'code': err.code, 'msg': err.message.strip()}

        print("  [5.1 触发器拦截验证]")
        print(f"       UPDATE 采用版拦截: ORA-{trigger_results['update_adoption']['code']}, Msg: {trigger_results['update_adoption']['msg']}")
        print(f"       DELETE 采用版拦截: ORA-{trigger_results['delete_adoption']['code']}, Msg: {trigger_results['delete_adoption']['msg']}")
        print(f"       UPDATE 审计表拦截: ORA-{trigger_results['update_audit']['code']}, Msg: {trigger_results['update_audit']['msg']}")
        print(f"       DELETE 审计表拦截: ORA-{trigger_results['delete_audit']['code']}, Msg: {trigger_results['delete_audit']['msg']}")
        
        assert trigger_results['update_adoption']['code'] == 20001
        assert trigger_results['delete_adoption']['code'] == 20001
        assert trigger_results['update_audit']['code'] == 20003
        assert trigger_results['delete_audit']['code'] == 20003

        # 5. 体积估算：模拟生成一个完整规模的 Bonding 采用版
        # 典型规模（见 Notes）：18 条线体，40 个机种，15 周（105 天）生产计划
        # 实际每日线体约有 1~3 个机种安排，按全窗口 18线 × 105天 × 2个机种 = 3,780 关系行
        # 快照 JSON（元数据、指标汇总、需求版本引用、参数快照哈希）约 20KB~50KB
        test_adopt_no = 999
        adopt_rows = []
        import datetime
        start_dt = datetime.date(2026, 10, 19)
        for d in range(105): # 15周
            cur_date = (start_dt + datetime.timedelta(days=d)).strftime('%Y-%m-%d')
            for l_idx in range(1, 19): # 18条线体
                line_id = f"LINE_{l_idx:02d}"
                # 安排 2 个机种
                adopt_rows.append((test_adopt_no, line_id, cur_date, f"MODEL_{((l_idx+d)%40)+1:02d}", 1500))
                adopt_rows.append((test_adopt_no, line_id, cur_date, f"MODEL_{((l_idx+d+1)%40)+1:02d}", 1200))
        
        total_rows = len(adopt_rows)
        meta_payload = json.dumps({
            'meta': 'Bonding Full 15-Week Adoption Snapshot',
            'lines_count': 18,
            'models_count': 40,
            'window_days': 105,
            'total_scheduled_qty': sum(r[4] for r in adopt_rows),
            'metrics': {'on_time_rate': 0.985, 'changeover_count': 45},
            'input_snapshot_sha256': 'a1b2c3d4e5f678901234567890abcdef1234567890abcdef1234567890abcdef'
        })

        t0 = time.time()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO PROTO_PLAN_ADOPTION (ADOPTION_NO, BASE_REVISION, ADOPTED_BY, SNAPSHOT_META)
                VALUES (:1, 1, 'BENCHMARK_USER', :2)
            """, (test_adopt_no, meta_payload))
            
            # 批量插入关系行
            cur.executemany("""
                INSERT INTO PROTO_PLAN_ADOPTION_ROW (ADOPTION_NO, LINE_ID, PROD_DATE, MODEL_ID, QUANTITY)
                VALUES (:1, :2, :3, :4, :5)
            """, adopt_rows)
            conn.commit()
        insert_duration = time.time() - t0

        # 查询统计该采用版在数据库中的实际行数与物理存储占用
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM PROTO_PLAN_ADOPTION_ROW WHERE ADOPTION_NO = :1", (test_adopt_no,))
            inserted_count = cur.fetchone()[0]
            
            # 计算行数据的平均长度
            cur.execute("""
                SELECT AVG(VSIZE(LINE_ID) + VSIZE(PROD_DATE) + VSIZE(MODEL_ID) + VSIZE(QUANTITY) + VSIZE(ADOPTION_NO))
                FROM PROTO_PLAN_ADOPTION_ROW WHERE ADOPTION_NO = :1
            """, (test_adopt_no,))
            avg_row_bytes = cur.fetchone()[0]
            
            # 计算元数据 CLOB 长度
            cur.execute("SELECT DBMS_LOB.GETLENGTH(SNAPSHOT_META) FROM PROTO_PLAN_ADOPTION WHERE ADOPTION_NO = :1", (test_adopt_no,))
            clob_bytes = cur.fetchone()[0]

        total_est_data_bytes = (inserted_count * avg_row_bytes) + clob_bytes
        print(f"  [5.2 采用版单版体积实测]")
        print(f"       全窗口 15 周 18 线体排程总行数: {inserted_count} 行 (耗时 {insert_duration:.3f}s)")
        print(f"       关系行平均行净大小: {avg_row_bytes:.1f} bytes")
        print(f"       关系行总净数据量: {(inserted_count * avg_row_bytes) / 1024:.2f} KB")
        print(f"       快照元数据 CLOB 大小: {clob_bytes / 1024:.2f} KB")
        print(f"       单采用版估算净体积: {total_est_data_bytes / 1024:.2f} KB (~{total_est_data_bytes / (1024*1024):.3f} MB)")
        
        # 按每年采用 100 次（每周 2 次正式采用）推算
        yearly_adoptions = 100
        yearly_mb = (total_est_data_bytes * yearly_adoptions) / (1024 * 1024)
        # 加索引与行头开销因子约 2.5 倍
        yearly_storage_with_index = yearly_mb * 2.5
        print(f"       容量推算: 每年按 100 个采用版计，净数据 ~{yearly_mb:.2f} MB，含主键索引实际表空间占用约 ~{yearly_storage_with_index:.2f} MB")
        
        volume_res = {
            'rows_count': inserted_count,
            'avg_row_bytes': round(avg_row_bytes, 1),
            'clob_bytes': clob_bytes,
            'single_adoption_kb': round(total_est_data_bytes / 1024, 2),
            'yearly_100_adoptions_mb': round(yearly_storage_with_index, 2)
        }
    finally:
        pool.release(conn)

    return {'triggers': trigger_results, 'volume': volume_res}


# =========================================================================
# 验证项 6: oracledb 厚模式连接池在 5 并发下的耗时分位数与表空间配额核实
# =========================================================================
def test_item_6_pool_concurrency_and_tablespace(pool):
    print("\n--- [Test 6] 5 并发下 oracledb 厚模式连接池耗时分位数与表空间/配额实际情况 ---")

    # 1. 表空间与配额核实
    conn = pool.acquire()
    tablespace_info = {}
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT tablespace_name, bytes, max_bytes 
                FROM user_ts_quotas
            """)
            quotas = cur.fetchall()
            cur.execute("""
                SELECT username, default_tablespace, temporary_tablespace
                FROM user_users
            """)
            user_u = cur.fetchone()
            
            # 查一下当前用户的段总大小
            cur.execute("""
                SELECT segment_type, COUNT(*), SUM(bytes)
                FROM user_segments
                GROUP BY segment_type
            """)
            segs = cur.fetchall()

            tablespace_info = {
                'user': user_u[0],
                'default_tablespace': user_u[1],
                'temp_tablespace': user_u[2],
                'quotas': [{'tablespace': q[0], 'used_bytes': q[1], 'max_bytes': 'UNLIMITED' if q[2] == -1 else q[2]} for q in quotas],
                'segments': [{'type': s[0], 'count': s[1], 'bytes': s[2]} for s in segs]
            }
            print(f"  [6.1 表空间与配额核实]")
            print(f"       用户: {user_u[0]}, 默认表空间: {user_u[1]}, 临时表空间: {user_u[2]}")
            for q in tablespace_info['quotas']:
                print(f"       配额: 表空间 {q['tablespace']}, 已用 {q['used_bytes']} bytes, 上限 {q['max_bytes']}")
    finally:
        pool.release(conn)

    # 2. 5 并发压力与耗时分位数实测
    # 模拟 5 个并发客户端，每个客户端连续执行 20 次完整的“获取连接 -> 锁头行 -> 检查范围键 -> 写入草稿与范围键 -> 提交 -> 释放连接”
    # 记录每次事务从发起请求到完成提交的总耗时（单位：毫秒）
    concurrency = 5
    requests_per_worker = 20
    latencies = []
    lock = threading.Lock()

    def worker_stress(worker_id):
        worker_latencies = []
        for i in range(requests_per_worker):
            t_start = time.perf_counter()
            c = pool.acquire()
            try:
                with c.cursor() as cur:
                    # 锁头行
                    cur.execute("SELECT CURRENT_REVISION FROM PROTO_PLAN_HEADER WHERE PLAN_ID = 'BONDING_MAIN' FOR UPDATE WAIT 10")
                    cur_rev = cur.fetchone()[0]
                    
                    new_rev = cur_rev + 1
                    # 写入新修订
                    cur.execute("""
                        INSERT INTO PROTO_PLAN_DRAFT_REVISION (REVISION, PARENT_REVISION, AUTHOR, SUMMARY)
                        VALUES (:1, :2, :3, :4)
                    """, (new_rev, cur_rev, f"STRESS_W{worker_id}", f"Stress test round {i}"))
                    
                    # 写入一个范围键
                    cur.execute("""
                        INSERT INTO PROTO_PLAN_SCOPE_KEY (REVISION, SCOPE_KEY)
                        VALUES (:1, :2)
                    """, (new_rev, f"STRESS_KEY_{worker_id}_{i}"))

                    # 更新头行
                    cur.execute("""
                        UPDATE PROTO_PLAN_HEADER
                        SET CURRENT_REVISION = :1, UPDATED_AT = SYSTIMESTAMP
                        WHERE PLAN_ID = 'BONDING_MAIN'
                    """, (new_rev,))
                    
                    c.commit()
            except Exception as e:
                c.rollback()
                print(f"Worker {worker_id} err:", e)
            finally:
                pool.release(c)
                duration_ms = (time.perf_counter() - t_start) * 1000.0
                worker_latencies.append(duration_ms)
        
        with lock:
            latencies.extend(worker_latencies)

    threads = [threading.Thread(target=worker_stress, args=(w,)) for w in range(concurrency)]
    t0_all = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total_stress_time = time.time() - t0_all

    latencies.sort()
    n = len(latencies)
    p50 = latencies[int(n * 0.50)]
    p90 = latencies[int(n * 0.90)]
    p95 = latencies[int(n * 0.95)]
    p99 = latencies[int(n * 0.99)]
    avg_lat = statistics.mean(latencies)
    min_lat = min(latencies)
    max_lat = max(latencies)

    print(f"  [6.2 5 并发厚模式连接池耗时实测]")
    print(f"       完成请求总数: {n} 次, 总耗时: {total_stress_time:.2f}s, 吞吐量: {n/total_stress_time:.1f} TPS")
    print(f"       耗时分位数 (ms):")
    print(f"         Min: {min_lat:.2f} ms")
    print(f"         P50: {p50:.2f} ms")
    print(f"         P90: {p90:.2f} ms")
    print(f"         P95: {p95:.2f} ms")
    print(f"         P99: {p99:.2f} ms")
    print(f"         Max: {max_lat:.2f} ms")
    print(f"         Avg: {avg_lat:.2f} ms")

    perf_stats = {
        'total_requests': n,
        'total_time_s': round(total_stress_time, 2),
        'tps': round(n / total_stress_time, 1),
        'min_ms': round(min_lat, 2),
        'p50_ms': round(p50, 2),
        'p90_ms': round(p90, 2),
        'p95_ms': round(p95, 2),
        'p99_ms': round(p99, 2),
        'max_ms': round(max_lat, 2),
        'avg_ms': round(avg_lat, 2)
    }

    return {'tablespace': tablespace_info, 'performance': perf_stats}

def main():
    print("=========================================================================")
    print("开始执行 Oracle 行锁、幂等键作废与采用竞争验证测试套件 (PLAN_TEST)")
    print("=========================================================================")
    conn = get_connection()
    setup_schema(conn)
    conn.close()

    pool = get_pool(min_pool=2, max_pool=10)
    summary = {}
    try:
        summary['test_1'] = test_item_1_concurrent_adoption(pool)
        summary['test_2'] = test_item_2_idempotency_replay(pool)
        summary['test_3'] = test_item_3_network_disconnect_and_verification_void(pool)
        summary['test_4'] = test_item_4_scope_keys_conflict(pool)
        summary['test_5'] = test_item_5_trigger_immutability_and_volume(pool)
        summary['test_6'] = test_item_6_pool_concurrency_and_tablespace(pool)
    finally:
        pool.close()

    print("\n=========================================================================")
    print("测试套件全部执行成功！全部断言通过。")
    print("=========================================================================")
    with open('/tmp/oracle_repo/test_results.json', 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("详细结果已保存至 /tmp/oracle_repo/test_results.json")

if __name__ == '__main__':
    main()
