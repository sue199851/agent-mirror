# README 首屏插入建议

放置位置：README.md 的项目标题、定位句与徽标之后，第一个功能介绍章节之前。以下片段可直接粘贴到仓库根目录 README.md；仅引用报告首屏，避免与现有标题重复。

```markdown
![Agent Mirror 示例画像全链路：从本地会话数据，经知识库检索、性格证据分析与当事人校验，到职业罗盘](assets/readme-hero.png)

*示例数据的画像全链路预览；五维签名仅用于说明展示形式，不是人格定论。*
```

如果首屏更适合品牌定位卡，可用下面片段替换上面的报告图片（不建议两张同时放在首屏）：

```markdown
![Agent Mirror：你的 AI 对话史 = 比日记更诚实的行为日志；知识库 · 性格镜像 · 职业罗盘，全程本地](assets/og-card.png)
```

## 社交预览设置与制作信息

- `assets/og-card.png`：1200 × 640，深色网格、项目定位、示例 O+ C+ E+ A N 签名与 100% local 文案；在 GitHub 仓库 Settings → General → Social preview 手动上传，不会因 README 引用而自动设置。
- `assets/og-card.html`：社交卡的可复现本地 HTML 源文件，使用系统 Noto Sans CJK SC 中文字体。
- `assets/readme-hero.png`：1200 × 850，直接由只读的 `demo/report/portrait.html?theme=dark&embed=1` 渲染，显示完整流程图缩略。
- 两张 PNG 均使用系统 Google Chrome 无头模式本地截图，设备缩放比例 1；未修改示例页面或 README。
- 两图合计 319,367 字节，低于 2 MB；已使用 `file` 验证 PNG 格式和尺寸，并人工查看图片确认中文与流程图正常显示。
