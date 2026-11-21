# 专线改道演练服务（Circuit Migration Rehearsal）

用 FastAPI 实现的改道演练服务：提交有向链路与容量、2～10 条隧道的带宽、以及每条隧道**起终点相同的现用路径和目标路径**，服务返回演练号与一条能全程走通的改道顺序；无解则拒绝并说明原因，不建立演练。

## 启动

```bash
docker compose up --build
```

打开 <http://localhost:8000/docs> 即可在 Swagger 页面提交、确认与查看。数据落在命名卷 `rehearsal-data` 的 SQLite 文件中，容器重启后演练状态仍在。

本地直接运行：

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload        # DB_PATH 默认 data/migrations.db
python -m pytest                     # 34 个测试
```

## 计费规则

* 全部容量、带宽均为**整数 Mbps**。
* 每条隧道的现用路径持续占用带宽，直到该隧道**确认切换**为止。
* 目标路径与现用路径**共有的链路只计一次**：预留时只对「目标路径独有」的链路扣带宽，切换时旧路独有链路的带宽自动释放。
* 一条路径内同一链路重复出现会被拒绝（避免自环与重复计费）。

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `POST` | `/rehearsals` | 提交演练。成功 `201`，返回 `rehearsal_id` 与 `order`/`steps`；无解 `409 no_feasible_order`（不创建）；结构非法 `422 invalid_scenario` |
| `GET` | `/rehearsals/{id}` | 查看现用路径，以及每条链路的 `used` / `reserved` / `available` |
| `POST` | `/rehearsals/{id}/reservations` | 为**顺序中的下一条**隧道预留带宽，返回预留编号；同一时刻只允许一笔待确认预留 |
| `POST` | `/rehearsals/{id}/reservations/{rid}/confirm` | 凭编号确认：切换路径并释放旧路独有带宽 |
| `POST` | `/rehearsals/{id}/reservations/{rid}/cancel` | 凭编号取消：只撤回本次预留，旧路不动，该步可重做 |
| `GET` | `/health` | 存活探针 |

### 状态约束

* **一次一笔**：已有待确认预留时，再发起预留返回 `409 step_conflict`，状态不变。
* **按序执行**：请求非「顺序中的下一条」隧道返回 `409 step_conflict`，状态不变。省略 `tunnel_id` 即自动取下一条。
* **编号一次性**：未知、已确认、已取消、不属于本次演练的编号，确认/取消均返回 `409 reservation_invalid`，状态不变。
* **取消可重做**：取消后演练回到该步之前，重新预留会得到新编号。
* 全部隧道切换后演练状态为 `completed`，再预留返回 `409`。

## 一次完整演练

```bash
BASE=http://localhost:8000
ID=$(curl -s -X POST $BASE/rehearsals -H 'Content-Type: application/json' \
       -d @examples/swap.json | python3 -c 'import sys,json;print(json.load(sys.stdin)["rehearsal_id"])')

# 1) 预留 → 2) 确认；重复到全部完成
RES=$(curl -s -X POST $BASE/rehearsals/$ID/reservations | python3 -c 'import sys,json;print(json.load(sys.stdin)["reservation_id"])')
curl -s -X POST $BASE/rehearsals/$ID/reservations/$RES/confirm
curl -s $BASE/rehearsals/$ID       # 查看各链路已用/预留带宽
```

`examples/infeasible.json` 是一个环形死锁（每条目标链路都被现用路径占满），提交会返回 `409` 且不建立演练。

## 负载格式

```json
{
  "links":    [{"link_id": "L1", "source": "A", "target": "B", "capacity": 10}],
  "tunnels":  [{"tunnel_id": "T1", "bandwidth": 4,
                "current_path": ["L1"], "target_path": ["L2"]}]
}
```

* `source`/`target` 可选：全部给出时会校验路径连续性、以及两条路径起终点一致；全部省略时仅按链路 id 计费。
* 兼容常见别名：`id`/`from`/`to`/`path`/`new_path`/`edges`/`circuits` 等。
* 错误响应统一为 `{"error": "<code>", "detail": "...", "errors": [...]}`。

## 实现要点

* `app/planner.py`：状态只取决于"哪些隧道已切换"，因此在 2^n（n≤10，至多 1024）个子集上做带失败记忆的 DFS，**精确**判定有无可行顺序并给出顺序；有随机场景与穷举对拍的测试。
* `app/engine.py`：所有状态变更在锁内完成，并以文档版本号做 compare-and-set 提交；并发下只会有一笔预留/一次确认成功。
* `app/store.py`：每次状态变更写一条 JSON 文档（SQLite 或内存），不会出现半更新。
