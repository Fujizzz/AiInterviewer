#!/usr/bin/env bash
# 职责：以 PostgreSQL 本机管理员身份创建一致的自定义格式备份，失败时明确退出。
# 实现：先写临时文件，校验归档目录后原子更名；不删除历史备份，不执行恢复或请求重试。
# 关联：数据库 ai_interviewer 与 /var/backups/ai-interviewer；需 root 执行。
# 目录：无函数或类。
# 变量索引：backup_dir 为备份目录；backup_path 为本次归档；pending_path 为未完成归档。
set -euo pipefail
umask 077
if [[ "${EUID}" -ne 0 ]]; then
    printf 'Backup requires root to use the local PostgreSQL administrative account.\n' >&2
    exit 1
fi
backup_dir=/var/backups/ai-interviewer
install -d -m 700 "$backup_dir"
backup_path="$backup_dir/ai_interviewer-$(date -u +%Y%m%dT%H%M%S)-$$.dump"
pending_path="$backup_path.pending"
trap 'printf "Backup failed at line %s; inspect %s if present.\n" "$LINENO" "$pending_path" >&2' ERR
# PostgreSQL 用户不需要读取调用者目录；从公共可遍历目录执行，避免 root 家目录权限告警。
cd /
runuser -u postgres -- pg_dump --format=custom ai_interviewer > "$pending_path"
pg_restore --list "$pending_path" > /dev/null
mv -- "$pending_path" "$backup_path"
printf 'PostgreSQL backup created: %s\n' "$backup_path"
