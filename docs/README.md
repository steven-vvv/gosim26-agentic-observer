# 巡天智能体（Agentic Observer）比赛平台页面存档

这里存放 GOSIM 2026 巡天智能体黑客松[比赛平台](https://create.gosim.org/survey26/platform/)上五个说明页面的 Markdown 版本，抓取日期为 2026-09-23，对应规则版本 1.0 · 2026-09-09。中文版放在 `zh/`，英文版放在 `en/`。

| 页面 | 中文 | English | 原始地址 |
|---|---|---|---|
| 赛事说明 / Brief | [zh/01-brief.md](zh/01-brief.md) | [en/01-brief.md](en/01-brief.md) | <https://create.gosim.org/survey26/platform/brief> |
| 规则 / Rules | [zh/02-rules.md](zh/02-rules.md) | [en/02-rules.md](en/02-rules.md) | <https://create.gosim.org/survey26/platform/rules> |
| 文档 / Docs | [zh/03-docs.md](zh/03-docs.md) | [en/03-docs.md](en/03-docs.md) | <https://create.gosim.org/survey26/platform/docs> |
| 资源 / Resources | [zh/04-resources.md](zh/04-resources.md) | [en/04-resources.md](en/04-resources.md) | <https://create.gosim.org/survey26/platform/resources> |
| 常见问题 / FAQ | [zh/05-faq.md](zh/05-faq.md) | [en/05-faq.md](en/05-faq.md) | <https://create.gosim.org/survey26/platform/faq> |

赛事宣传文章（OpenCamp 公众号）见上级目录的 [AD.md](../AD.md)。

## 抓取与转换方式

- 平台是 Vue 单页应用，页面内容在浏览器端渲染。抓取时用无头 Chromium 分别渲染 `?lang=zh` 和 `?lang=en`，取 `<main>` 部分转换为 Markdown。
- 「文档」和「规则」的正文在站点 JS 里就是 Markdown 源文，页面用 marked 原样渲染。这两页直接使用源文，没有经过 HTML 再转回 Markdown。
- 「文档」末尾的“协议速览”在页面上是三个标签页（initialize / decision_request / decision_response），存档里三页全部展开。
- 站内相对链接改成了 `https://create.gosim.org/survey26/platform/…` 形式的绝对地址。
- 删掉了纯装饰或交互用的元素：返回首页链接、复制按钮、页内目录、配图、步骤序号大字。页面顶部的栏目标签（kicker）写在每个文件 front matter 的 `section` 字段里。
- 转换结果经过脚本逐词比对。除了上面这些有意的调整，Markdown 与页面上的可见文字一致。

## 注意事项

1. **有内容未收录**：站点有一个 `mechanics_public` 开关，抓取时处于关闭状态。受它控制的内容（赛事说明中关于正式赛新机制的 1 节，FAQ 中关于正式赛异常机制的 2 条）页面上不显示，所以存档里也没有。开关打开后需要重新抓取。
2. **中英文不一致**（源站问题，存档原样保留）：
   - 队伍人数：英文版规则第 1 节写的是 “A team has 1 to 8 members”；中文版规则、中英文 FAQ 和建队表单写的都是 1–3 人。
   - 协议版本：中文版规则第 3 节第 2 条写明练习场景仍用 `participant-agent-protocol-v1`、异常机制（v2）只在正式比赛场景启用；英文版同一条写的是所有场景都用 v2 运行。中英文文档页第 8 节都说练习场景仍为 v1，与中文版规则一致。
3. **实时数据**：「规则」里的阶段配置表和「资源」里的场景列表来自平台数据库，记录的是抓取时的状态。
4. **场景文件**：「资源」页上的场景文件要点页面按钮（由 JS）下载，存档里只列了文件名。需要文件时可以用入门包里的 `fetch_scenario.py` 下载。
