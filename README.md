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

测试包括：分配、标注、发现分歧、阻止提前冻结、仲裁、计算一致性、冻结和导出；另一条测试验证提交答案前后讨论可见性变化，以及错误角色不能领取标注任务。返工测试覆盖：管理员发起返工、待处理期间禁止重复建单与冻结、重新提交后返工完成、每次标签与抽检记录随冻结导出留痕。

## 接口

- `POST /api/users`、`POST /api/guidelines`、`POST /api/batches`
- `POST /api/batches/{id}/items`、`POST /api/batches/{id}/assign`
- `POST /api/annotations`、`POST /api/adjudications`
- `POST /api/reworks`、`GET /api/reworks?status=pending|done&batch_id=`（同 `/api/batches/{id}/reworks`）
- `GET /api/items/{id}?user_id=`
- `GET /api/batches/{id}/disagreements`
- `GET /api/batches/{id}/consistency`
- `POST /api/batches/{id}/freeze`
- `GET /api/batches/{id}/gold`

一致性同时返回逐条成对一致率和 Fleiss Kappa。冻结要求每条至少有两人标注、没有未仲裁分歧，且不存在待处理返工；冻结后不能修改标注或发起返工，导出结果来自不可变的 `gold_records`。

## 抽检返工

管理员可对**已提交**的标注发起返工单（`POST /api/reworks`，必填 `reason` 意见）。同一标注在待处理期间不能重复建单，标注员通过常规 `POST /api/annotations` 重新提交后返工单自动结束（记录新标签与完成时间）；存在待返工时批次禁止冻结。

每次提交的标签都会作为独立版本留痕（旧版本置 `superseded=1`，不参与分歧与一致性计算）。冻结时将每条的全部标签版本与返工记录（抽检原因、原标签、新标签、发起/完成时间）快照写入 `gold_records`，导出在每条记录的 `annotations`、`reworks` 字段中原样返回。页面「抽检返工」区块可发起返工、查看待返工，并按状态（待处理/已处理）和批次筛选。
