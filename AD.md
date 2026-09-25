# GOSIM 2026 巡天智能体黑客松正式启动：让 AI 决定巡天望远镜下一步看哪里，5,500 美元奖金池等你来战！

> **来源：** [OpenCamp](https://mp.weixin.qq.com/s?__biz=MzIwMzcwODA2Mw==&mid=2247485774&idx=1&sn=ef696a9620b80117e00fe700c5182115&chksm=9743290b3d74a1382258318e63c22076f3f7e6f248fe577dfff00551632f535ebb4fcae140fb&mpshare=1&srcid=0922UB8N2RQpEx0vomMoKi0q&sharer_shareinfo=c247254afc0b184674ff318be5679c12&sharer_shareinfo_first=c247254afc0b184674ff318be5679c12&from=groupmessage&scene=1&subscene=10000&sessionid=1790128433&clicktime=1790145292&enterid=1790145292&ascene=1&fasttmpl_type=0&fasttmpl_fullversion=8446176-zh_CN-zip&fasttmpl_flag=0&realreporttime=1790145292473&devicetype=android-36&version=28004845&nettype=cmnet&lang=zh_CN&countrycode=CN&exportkey=n_ChQIAhIQBANdyBLgJ1CLTLTNye6f8RL3AQIE97dBBAEAAAAAAGmzFrvs8PsAAAAOpnltbLcz9gKNyK89dVj0VIRgheQ6yjDMT4yd%252Fizzr5p30B2HwRmRHnVExGibd%252F30u6GvZXbTGY8tHMT6rTkfvtA80vTrbQNkiWI7gF9SAMSs195nSfIr7Nd8eeGI4FTuZd6aLTcafT6aE4SRw%252FqrAPxbFOxRqvpwx5Q6Ecww28bYmfJKMYRPMb1R1k8zbA9JZvuskgz5WUJTo1IEe3FnmoQGzXPVQS9NVnvTq8VjJYK1SRjtGBMdy2IMVuFxLyzyCyhu5boS2xgRYndABinbHTyHAIIEFcKUbexMwcPpp8E%253D&pass_ticket=G8SNbu30DgN8Klkj5bpR1L%252BX915n1PHF8qNEqJjU5gyd0pQNTasnV4CIYeFGuRMR8eRF%252BOJaN7jzlEY%252BJE6wMg%253D%253D&wx_header=3&utm_source=gemini)

*斗转星移，变化万千，你的天文望远镜，应该看向哪里？*

每一个晴朗的夜晚，天文台的观测员都在反复回答同一个问题：下一个时隙（900 秒），望远镜该对准哪一片天？天气在变，目标在升落，科学优先级各有轻重，而一次糟糕的选择，就会浪费珍贵的观测时间和资源。

现在，**GOSIM** 把这道题做成了一场面向全球开发者的黑客松——**巡天智能体（Agentic Observer）黑客松**。你要写一个智能体：读取天空状态，像资深观测员一样权衡，每一个 900 秒的时隙让他帮忙判断下一步观测什么。

---

## 📌 关键信息

* 💰 **奖金：** 5,500 美元，设 6 个奖项
* 🎁 **福利：** KIMI 顶级开源大模型支持 · 天文学与 AI 专家训练营指导 · 完赛证书
* 📅 **赛程：**
* 10 月 1–4 日：线上训练营
* 10 月 5–7 日：线上比赛
* 10 月 17 日：深圳颁奖


* ⏰ **报名截止：** 10 月 1 日，名额报满即止（Playground 已开放）
* 👥 **谁能参加：** 全球开放，个人或 1–3 人团队，无需天文背景
* 🚀 **怎么开始：** 注册 → 建队 → 下载入门包 → 20 分钟跑出第一个成绩

---

## 01 · 这是一场怎样的比赛？

现代巡天望远镜不是“对准一个目标，然后拍下来”那么简单。一个观测夜里，天气会变，目标会升落，不同科学任务对观测条件有不同要求；巡天本身还有自己的完成进度、区域覆盖要求和临时观测请求。

Agentic Observer 把这种权衡收敛成一个清晰的 AI 任务：读取当前状态 → 理解局势 → 比较候选 → 做出观测决策 → 环境继续变化 → 再次决策。每个决策时隙是 900 秒，也就是 15 分钟。你设计的不是一个只回答一次问题的 AI，而是一个在连续环境中持续做决定的观测智能体。

---

## 02 · 为什么让 AI 来做这件事？

Agent 的真正挑战在于“会持续决策”，而不仅仅是“会回答”。巡天观测正好是一个干净的实验场，它具备以下特点：

* **不确定性：** 天气和观测条件会变；
* **有限资源：** 每一个 900 秒的时隙都不可逆；
* **长期目标：** 整个巡天任务需要持续推进；
* **多目标权衡：** 科学价值、优先级和覆盖之间经常打架；
* **连续决策：** 每一个动作都会影响后续状态；
* **可量化结果：** 最终观测产出可以被统一打分。

---

## 03 · 你到底要做什么？

### 给你的智能体一个“观测员”的位置

比赛提供一套统一的巡天任务和模拟环境。在每个决策时隙，你的智能体都会拿到与当前决策相关的一组信息——当前天气与观测条件、已发布的天气预报、当前巡天任务的完成进度、当前可观测的候选天区，以及不同观测项目的要求——然后它需要决定下一步做什么。

比赛不限定技术路线，你可以从规则、排序和启发式方法起步，也可以再叠加记忆、预测、优化算法或大模型。

---

## 04 · 这场比赛有什么特别？

1. **解决真实前沿科学问题，而非科幻：** 赛题参考 DESI 等真实巡天任务的公开科学与运行设计，是一次 AI for Science 的决策实验。
2. **让 Agent 真正“跑起来”：** 重点在于让智能体实战运行巡天长时复杂任务，在一整夜里连续面对变化的环境。
3. **客观统一的量化评测机制：** 提供统一的任务定义、模拟器和评分体系。赛后平台会给出分数分解、逐夜运行结果、决策记录、运行日志及可视化。

---

## 05 · 谁适合参加？（无需天文学专业背景）

* 🤖 **AI / Agent 开发者：** 把智能体从“聊天”推进到“连续决策”。
* 💻 **Python 开发者：** 从简单策略起步，不断提高分数。
* 🌌 **天文 / AI for Science 爱好者：** 把科学问题变成 Agent 挑战。
* 🎓 **学生和初学者：** 提供入门工具包和线上培训。
* 🧠 **喜欢复杂决策问题的人：** 挑战在有限资源和不断变化环境里的选择。

---

## 06 · 你能获得什么？

* **5,500 美元奖金池**
* **开源模型 Token 支持：** 参赛队伍将获得开源模型 Token 支持，无需为调用成本发愁。
* **天文学 + 智能体双领域专家指导**
* **GOSIM 全球开源创新汇：** 排名前三的队伍将受邀参加 2026 年 10 月 17 日 GOSIM Shenzhen 颁奖活动。
* **完赛证书与成果展示**

---

## 07 · 赛程怎么走？

* **10 月 1 日：** 报名截止（北京时间 UTC+8）
* **10 月 1–4 日：** 线上训练营
* **10 月 5–7 日：** 线上比赛
* **10 月 17 日：** 深圳颁奖（GOSIM Shenzhen 2026 将于 10 月 16–17 日举行）

---

## 08 · 20 分钟，跑出你的第一个成绩

1. **报名：** 注册 Agentic Observer 比赛平台账号。
2. **建队：** 个人参赛也需要建立队伍，每队最多 3 人。
3. **下载入门工具包：** 跑通官方提供的基线程序。
4. **改你的策略：** 从最简单的选择规则开始。
5. **提交评测：** 提交到比赛平台。
6. **看结果，再改一次：** 观察分数、运行结果和决策记录进行优化。

---

## 09 · 现在就加入

* **竞赛官网：** [https://create.gosim.org/survey26/](https://create.gosim.org/survey26/?utm_source=gemini)
* **大会官网：** [https://shenzhen2026.gosim.org/](https://shenzhen2026.gosim.org/?utm_source=gemini)
* **联系邮箱：** hackathon@gosim.org