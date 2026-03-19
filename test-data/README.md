# test-data

本目录用于一键生成本地联调测试数据。

## 主要文件

- `seed_test_data.py`：导入脚本。
- `accounts.csv`：脚本运行后自动生成的账号密码清单（1 管理员 + 20 学生）。

## 脚本会做什么

1. 检查问卷；若数据库没有问卷，则自动生成 3 个分页、共 9 题（每页 3 题）。
2. 清空并重建业务测试数据（保留系统设置与问卷结构）：
   - 1 个管理员账号
   - 20 个学生账号（10 男 + 10 女）
   - 学生随机资料（qq/wechat/contact/mbti/province）
   - 每个学生都填写问卷，且每题随机权重
   - 每个性别：4 人满队 + 2 人半满队 + 4 人未组队
3. 生成学生头像到 `RMMT-API/static/uploads/student_avatar`
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
