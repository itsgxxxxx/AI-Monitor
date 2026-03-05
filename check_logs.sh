#!/bin/bash
# 日志检查脚本

LOG_FILE="logs/decision.jsonl"

echo "=========================================="
echo "AI Monitor 日志检查"
echo "=========================================="
echo ""

# 检查最近一次轮询
echo "📊 最近一次轮询信息："
if [ -f "$LOG_FILE" ]; then
    LAST_POLL=$(tail -1 "$LOG_FILE" | jq -r '.poll_id' 2>/dev/null)
    if [ -n "$LAST_POLL" ] && [ "$LAST_POLL" != "null" ]; then
        echo "   Poll ID: $LAST_POLL"
        echo ""

        # 统计本轮数据
        echo "📈 本轮统计："
        grep "\"poll_id\":\"$LAST_POLL\"" "$LOG_FILE" | jq -r '.stage' | sort | uniq -c | awk '{printf "   %-20s: %s\n", $2, $1}'
        echo ""

        # 推送成功数量
        echo "✅ 推送成功："
        PUSH_SUCCESS=$(grep "\"poll_id\":\"$LAST_POLL\"" "$LOG_FILE" | grep '"stage":"push"' | grep '"decision":"pass"' | wc -l | tr -d ' ')
        echo "   $PUSH_SUCCESS 条推文"
        echo ""

        # 过滤原因统计
        echo "🚫 过滤原因统计："
        grep "\"poll_id\":\"$LAST_POLL\"" "$LOG_FILE" | grep '"decision":"drop"' | jq -r '.reason_code' | sort | uniq -c | awk '{printf "   %-30s: %s\n", $2, $1}'
        echo ""

        # 重要性分级
        echo "⭐ 重要性分级："
        grep "\"poll_id\":\"$LAST_POLL\"" "$LOG_FILE" | grep '"stage":"importance"' | grep '"decision":"pass"' | jq -r '.reason_code' | sort | uniq -c | awk '{printf "   %-30s: %s\n", $2, $1}'
        echo ""

        # 最近推送的推文
        echo "📝 最近推送的推文（最多显示 5 条）："
        grep "\"poll_id\":\"$LAST_POLL\"" "$LOG_FILE" | grep '"stage":"push"' | grep '"decision":"pass"' | tail -5 | jq -r '"   @" + .account + " - " + .tweet_id'
        echo ""

    else
        echo "   ⚠️  未找到轮询记录"
    fi
else
    echo "   ❌ 日志文件不存在"
fi

echo "=========================================="
echo "💡 查询特定推文："
echo "   grep '推文ID' logs/decision.jsonl | jq ."
echo ""
echo "💡 查看所有轮询："
echo "   cat logs/decision.jsonl | jq -r '.poll_id' | sort -u"
echo ""
echo "💡 实时监控日志："
echo "   tail -f logs/decision.jsonl | jq ."
echo "=========================================="
