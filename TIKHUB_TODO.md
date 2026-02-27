# TikHub Twitter 集成 TODO

## 实施计划

### Phase 1: 基础实现
- [ ] 1. 新增 `sources/tikhub_twitter.py` - TikHub Twitter 数据源
- [ ] 2. 修改 `main.py` - 新增 `type: tikhub_twitter` 分支
- [ ] 3. 修改 `config.yaml` - 新增 tikhub 配置和账号列表
- [ ] 4. 重启服务测试基本拉取

### Phase 2: 高级功能
- [ ] 5. 实现自适应轮询频率（10min/30min）
- [ ] 6. 实现厂商识别逻辑（官方/创始人）
- [ ] 7. 实现内容分级（重大/常规）

### Phase 3: E2E 测试
- [ ] 8. 手动触发一次抓取
- [ ] 9. 验证 Telegram 推送
- [ ] 10. 检查输出格式正确性
