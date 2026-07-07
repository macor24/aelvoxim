#!/usr/bin/env bash
# 查看 Aelvoxim 对话历史
# 使用方法:
#   bash scripts/chat_history.sh              # 最近 10 个 session 概览
#   bash scripts/chat_history.sh detail <id>  # 看某个 session 的所有消息
set -euo pipefail

MODE="${1:-list}"
SESSION_ID="${2:-}"

PG_CONN="-d aelvoxim -U aelvoxim -h localhost"

case "$MODE" in
  list)
    echo "===== 最近 20 个对话 ====="
    PGPASSWORD=aelvoxim_pg_pass psql $PG_CONN -c "
      SELECT id, user_id, title, created_at
      FROM chat_sessions
      ORDER BY created_at DESC
      LIMIT 20;
    " 2>/dev/null || echo "（没有对话历史或数据库连接失败）"
    echo ""
    echo "===== 消息总数 ====="
    PGPASSWORD=aelvoxim_pg_pass psql $PG_CONN -c "
      SELECT COUNT(*) AS total_messages FROM chat_messages;
    " 2>/dev/null || echo "（没有消息数据）"
    ;;
  detail)
    if [ -z "$SESSION_ID" ]; then
      echo "用法: bash scripts/chat_history.sh detail <session_id>"
      exit 1
    fi
    echo "===== Session: $SESSION_ID ====="
    PGPASSWORD=aelvoxim_pg_pass psql $PG_CONN -c "
      SELECT id, role, left(content, 300) AS content_preview, created_at
      FROM chat_messages
      WHERE session_id = '$SESSION_ID'
      ORDER BY created_at ASC;
    " 2>/dev/null || echo "（查询失败）"
    ;;
  *)
    echo "用法: bash scripts/chat_history.sh [list|detail <session_id>]"
    ;;
esac
