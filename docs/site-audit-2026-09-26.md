# 比赛网站彩蛋与远程代码入口检查（2026-09-26）

检查对象：[当前比赛平台](https://create.gosim.org/survey26/platform/)及主办方[公开源码](https://github.com/gosimfoundation/hackathon-survey26/tree/60d6ca2f52ef3a14e9de31b9a0a48517d5410373) `60d6ca2f`（2026-09-26 03:03 UTC）。用无头 Chromium 验证了公开首页和不存在的路由；源码检查覆盖网页入口、动态加载、网络请求及回放组件。网页会继续部署更新，以下结论只对应这次检查时的版本。

## 隐藏的天文彩蛋

| 触发方式 | 效果 | 源码 |
| --- | --- | --- |
| 首页背景或文字非交互区 6 秒内点击 7 次；链接、按钮及 Sky Console 不计数 | 彗星提示和全屏流星雨 | [HeroSection.vue](https://github.com/gosimfoundation/hackathon-survey26/blob/ddc0d42c/web/src/components/sections/HeroSection.vue) |
| 点击页面底部 `900 S` | 生成一条随机的 900 秒天文事实，9 秒后消失 | [AppFooter.vue](https://github.com/gosimfoundation/hackathon-survey26/blob/60d6ca2f/web/src/components/layout/AppFooter.vue) |
| 打开任意不存在的站内地址，例如 `/survey26/platform/where-is-m42` | 404 页随机介绍一个梅西耶天体（M31、M42、M45、M13、M51、M104、M1、M81） | [NotFoundPage.vue](https://github.com/gosimfoundation/hackathon-survey26/blob/60d6ca2f/web/src/pages/NotFoundPage.vue) |
| 打开开发者控制台 | ASCII 图案及旅行者 1 号飞行天数 | [eggs.ts](https://github.com/gosimfoundation/hackathon-survey26/blob/60d6ca2f/web/src/lib/eggs.ts) |
| 查看标签页图标；满月时查看桌面宽度导航栏 | 随实时月相变化的 favicon；满月徽标（`?moon=full` 可预览） | [eggs.ts](https://github.com/gosimfoundation/hackathon-survey26/blob/60d6ca2f/web/src/lib/eggs.ts)、[AppHeader.vue](https://github.com/gosimfoundation/hackathon-survey26/blob/60d6ca2f/web/src/components/layout/AppHeader.vue) |
| 中秋节当天及后两天进入网站 | 可关闭的中秋问候；`?egg=midautumn` 可预览 | [MidAutumnEgg.vue](https://github.com/gosimfoundation/hackathon-survey26/blob/60d6ca2f/web/src/components/layout/MidAutumnEgg.vue) |

中秋问候被关闭后会在本年度记住；自动化浏览器默认看不到，除非带预览参数。彩蛋均在公开网页源码中实现，没有发现影响评分规则的隐藏触发器。

旧版部署曾因 `.cosmos-footer { overflow: hidden }` 裁掉向上弹出的事实框；当前部署已将横向裁剪与纵向溢出分开，点击 `900 S` 可以正常看到提示。此前的复现记录保留在历史提交 `ddc0d42c` 对应的版本中。

## 远程内容与可执行路径

- 实际浏览器加载的脚本为本站 `restore-route.js`、带哈希的 `/assets/*.js` 及其动态加载块（包括 Three.js 和 404 页）。本次公开路由访问未观察到第三方脚本或从接口取回 JavaScript 后 `eval` 的路径。另有 GitHub 头像、字体及 Supabase 数据请求；这些不是浏览器脚本入口。
- [freshness.ts](https://github.com/gosimfoundation/hackathon-survey26/blob/60d6ca2f/web/src/lib/freshness.ts) 定期读取**同源** `index.html?fresh=...`，发现新构建后刷新页面。因此主办方部署新网页版本时，已有标签页可能自动更新并执行新版本的本站脚本；这是明确的更新路径。
- 比赛模式、阶段和部分展示内容来自 [Supabase](https://github.com/gosimfoundation/hackathon-survey26/blob/60d6ca2f/web/src/stores/competition.ts)；服务器可以开启现有的项目提交工作流。该工作流把参赛项目 ZIP 或指定 GitHub 提交交给主办方的运行器执行（[observer-prepare.ts](https://github.com/gosimfoundation/hackathon-survey26/blob/60d6ca2f/supabase/functions/_shared/observer-prepare.ts)），这是正式比赛设计中的参赛代码执行，不是隐蔽的浏览器脚本。
- [ReplayViewer.vue](https://github.com/gosimfoundation/hackathon-survey26/blob/60d6ca2f/web/src/components/submissions/ReplayViewer.vue) 经用户点击后，从私有 `results` 存储读取 `decision_replay.html`，放入 `sandbox="allow-scripts"` 的 iframe；“新标签页”按钮将同一 HTML 作为 Blob 页面打开。当前服务端用固定的[回放模板](https://github.com/gosimfoundation/hackathon-survey26/blob/60d6ca2f/challenge/templates/decision_replay.html)生成它，该模板含内联脚本。这里是远程 HTML/脚本内容的明确交付位置；若生成端或结果文件不可信，新标签页比沙箱 iframe 有更大风险（Blob URL 继承创建它的站点来源，`noopener` 只隔离 opener）。没有发现当前版本利用此路径注入额外脚本的证据。
- 场景文件来自 Supabase `scenarios` 存储，结果文件来自 `results` 存储；下载或读取的数据本身不等于执行代码。资源页的入门包、`scoring_core.py` 和 `SKILL.md` 是用户显式下载的静态文件。

此次复核还确认，资源页新增的 `dev-fortnight` 是公开练习数据，规则页新增的“完整项目”轨道会在云端执行参赛程序；两者都属于公开说明的比赛功能，不是隐藏下发位置。核心评分器与参赛运行时在本次源码更新中没有变更。

以上只覆盖公开可访问的浏览器流程与这份源码，不能证明受登录权限保护的每条路径或未来部署都保持相同内容。
