# 语料标注与争议仲裁

项目使用 Python 标准库、SQLite 和 `http.server`，实现批次、指南版本、重复标注、分歧检测、仲裁、一致性指标、金标准冻结与导出，并以“提交前不可查看含答案讨论”的方式隔离讨论区答案。

## 启动

```bash
python app.py
```

默认地址 <http://127.0.0.1:8112>，默认数据库为 `corpus.db`。首次启动会写入两位标注员、一位仲裁员和一个含分歧的示例批次。

```bash
PORT=9002 CORPUS_DB=/tmp/corpus.db python app.py
```

## 测试

```bash
python -m unittest discover -s tests -v
```

测试包括：分配、标注、发现分歧、阻止提前冻结、仲裁、计算一致性、冻结和导出；另一条测试验证提交答案前后讨论可见性变化，以及错误角色不能领取标注任务。

## 接口

- `POST /api/users`、`POST /api/guidelines`、`POST /api/batches`
- `POST /api/batches/{id}/items`、`POST /api/batches/{id}/assign`
- `POST /api/annotations`、`POST /api/adjudications`
- `GET /api/items/{id}?user_id=`
- `GET /api/batches/{id}/disagreements`
- `GET /api/batches/{id}/consistency`
- `POST /api/batches/{id}/freeze`
- `GET /api/batches/{id}/gold`
- `POST /api/reworks`、`GET /api/batches/{id}/reworks?status=pending|done`

一致性同时返回逐条成对一致率和 Fleiss Kappa。冻结要求每条至少有两人标注、没有未仲裁分歧、没有待处理返工；冻结后不能修改标注，导出结果来自不可变的 `gold_records`。

## 抽检返工

管理员可对已提交标注发起返工单（`POST /api/reworks`，含抽检意见）。同一标注（条目+标注员）在待处理期间不能重复建单（部分唯一索引兜底）；标注员在条目详情中可看到待返工意见，重新提交标注后工单才置为已处理并记录完成时间。存在待返工时批次不能冻结。冻结时会把每次标签修订（`annotation_revisions` → `gold_annotations`）和全部返工单（抽检原因、发起/完成时间，`reworks` → `gold_reworks`）快照固化，导出接口 `records` 之外另返回 `annotations` 与 `reworks` 两个历史列表。页面“抽检返工”区可发起返工、查看待返工并按状态筛选已处理记录。
