# test-data

本目录用于一键生成本地联调测试数据。

## 主要文件

- `seed_test_data.py`：导入脚本。
- `accounts.csv`：脚本运行后自动生成的账号密码清单（1 管理员 + 120 学生）。

## 脚本会做什么

1. 重建 MVP 问卷，默认生成 6 个分页、共 19 题。
2. 清空并重建业务测试数据（保留系统设置与问卷结构）：
   - 1 个管理员账号
   - 120 个学生账号（60 男 + 60 女）
   - 学生随机资料（qq/wechat/contact/mbti/province；contact 为三个词、分号分隔）
   - 每个学生都填写问卷，问卷题目默认权重为 1，学生答案权重在 1-3 间随机
   - 每个性别：2 个 4 人满队 + 8 个 3 人队 + 10 个 2 人队 + 其余未组队
3. 为**非满员队**学生生成随机色头像到 `RMMT-API/static/uploads/student_avatar`（满员队学生无头像文件）
4. 生成账号表 `RMMT-API/test-data/accounts.csv`

## 运行示例

```bash
cd /home/xxc/projects/RMMT-jaredenv1/RMMT-API
source venv/bin/activate
python test-data/seed_test_data.py \
  --db-host 172.18.0.2 \
  --db-port 3306 \
  --db-user root \
  --db-password 'REPLACE_WITH_DB_PASSWORD' \
  --db-name roommate
```

> 说明：`test-data` 位于 `RMMT-API/test-data`，在 `RMMT-API` 目录执行时请使用 `python test-data/seed_test_data.py ...`。
