"""参考实现 · Reference strategy — 一份写完整、讲清楚、可以被你超过的示范。

用法：把这个文件复制成 my_strategy.py 就能直接跑。
Usage: copy this file over my_strategy.py and run it.

━━━ 它在干什么 ━━━

平台给的候选已经按 `estimated_gain_per_second` 排好了——「这一枪每秒赚多少分」。
这个排序只看眼前，看不见两类要到整场结束才结算的账：

  · **罚分**：漏一块必做天区 −1000；某分区可选天区不足 4 块，每缺一块 −100；请求过期 −190/块
  · **覆盖均匀性**：`coverage_bonus = W · base_science · E`，E 是已完成天区在 8 个分区间的
    均匀度（Jain 公平指数）。正式比赛 W = 0.35，这一项约占总分两成；练习场景 W = 0，不生效。

实测（1600 天区的比赛规模场景）：会算覆盖边际收益的策略比贪心基线高 **+3980 分（+2.00%）**，
而且两个维度同时赢——科学分 137674 vs 136292，均匀度 0.856 vs 0.811。

反过来，有些看着合理的规则实测是掉分的，文件里用 `if False` 标出来了，你可以自己打开做对照。
「天况不好就主动等」尤其危险：等待每秒只扣 0.001，但错过的观测窗口不会回来，实测掉 1700 分。

━━━ 默认排序缺什么 ━━━

平台给的候选已经按 `estimated_gain_per_second` 排好了——「这一枪每秒赚多少分」。
这个排序只看眼前，看不到三笔要到整场结束才结算的账：

  · 漏掉一块必做天区        −1000
  · 某分区可选天区不足 4 块  每缺一块 −100
  · 观测请求过期            −190/块（完成则 +140/块）

所以这份实现只做一件事：**平时信任平台的排序，只有当某块天区「再不拍就真的要吃罚分」
时才推翻它。** 没有理由就不乱动——实测表明，在已经最优的排序上瞎加权重只会掉分。

用到的信息全部来自平台发给你的决策快照，没有任何隐藏数据，只用标准库。

━━━ 异常上报（不归这个文件管）━━━

故障/nova/红化的检测与上报在决策管线层（`anomaly_detection.py`）里做，`my_strategy.py` 不需要操心。
看一眼它的判据有助于理解为什么要**保守**：

  · 每条完成的观测都有公开公式基线（提交那一刻的快照估值），`tile_last_finished` 给你实现分。
    比值 ≈1.5 是 nova，≈0.8 是红化，≤0.5 量级是仪器故障。
  · 标签是永久的，天气抖动是暂时的。所以上报一条标签前，管线要求该 tile 的历次读数里
    至少三分之二落在标签区间（至少 3 次读数）——真标签几乎每次都落在区间内，
    而曝光中途的天气突变只会偶尔掉进去。
  · 错报一条标签 −150，对一条 +100：瞎猜的期望是负的（猜中率要到 60% 才不亏）。
    故障误报有账本法：两次正确上报之间只有一次免费误报额度。

在参考场景（1 次持续故障、2 个 nova、2 个红化）上，这套保守判据跑完全部 4 个标签 +400 分、
故障正确上报、0 误报、0 错报。你自己写检测逻辑时，先把「不确定就不报」刻在墙上。
"""

from datetime import datetime

# 这些数字来自公开的 score_config.json；比赛配置改了记得跟着改
MISS_REQUIRED = 1000.0      # 每块没完成的必做天区
SHORT_FLEXIBLE = 100.0      # 每块可选天区缺额
FLEXIBLE_QUOTA = 4          # 每个分区需要完成的可选天区数
REQUEST_REWARD = 140.0      # 完成请求，每块所需天区
REQUEST_MISS = 190.0        # 请求过期，每块所需天区

# 机会少到这个数就算「告急」，值得推翻平台排序
LAST_CHANCES = 2


def _utc(value):
    """协议里的 ISO 时间串 → 秒。解析不了就返回 None，由调用方兜底。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def remaining_chances(snapshot, now):
    """每块天区在「已公布的窗口」里还剩几次机会。

    `night_start.tile_windows` 是今夜的窗口，`weekly.tile_windows` 是未来一周的。平台只发布到
    这个范围，所以这就是你能合法看到的全部前瞻信息。数出来越少，现在越该动手。
    """
    counts = {}
    weekly = (snapshot.get("weekly") or {}).get("tile_windows") or []
    tonight = (snapshot.get("night_start") or {}).get("tile_windows") or []
    for window in list(weekly) + list(tonight):
        end = _utc(window.get("window_end_utc"))
        if end is not None and end <= now:
            continue  # 已经过去的窗口不算机会
        tile_id = window.get("tile_id")
        if tile_id:
            counts[tile_id] = counts.get(tile_id, 0) + 1
    return counts


def region_shortfall(snapshot):
    """每个分区还差几块可选天区才够配额。差得越多，这个分区的候选越值钱。"""
    done = (snapshot.get("progress") or {}).get("flexible_completed_by_region") or {}
    return {
        region: max(0, FLEXIBLE_QUOTA - int(count or 0))
        for region, count in done.items()
    }


def expiring_requests(snapshot, now, within_days=1.0):
    """快到期的观测请求。完成 +140/块、过期 −190/块，一来一回 330 分，值得插队。"""
    urgent = set()
    for request in snapshot.get("active_requests") or []:
        request_id = request.get("request_id")
        if not request_id:
            continue
        for key in ("deadline_utc", "expires_at_utc", "window_end_utc", "required_by_utc", "due_utc"):
            deadline = _utc(request.get(key))
            if deadline is None:
                continue
            if (deadline - now) / 86400.0 <= within_days:
                urgent.add(request_id)
            break
    return urgent


def coverage_weight(snapshot):
    """比赛场景把覆盖均匀性的权重放在 score_config 里；练习场景没有这一项，返回 0。"""
    for key in ("score_config", "scoring", "competition"):
        block = snapshot.get(key)
        if isinstance(block, dict) and "coverage_bonus_weight" in block:
            try:
                return float(block["coverage_bonus_weight"])
            except (TypeError, ValueError):
                return 0.0
    try:
        return float(snapshot.get("coverage_bonus_weight") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _evenness_gain(done_by_region, region, n_regions):
    """再拍这个分区一块，Jain 公平指数会涨多少（涨得越多越值得拍）。"""
    if region is None:
        return 0.0
    counts = list(done_by_region.values())
    total = sum(counts)
    squares = sum(value * value for value in counts)
    if total <= 0:
        return 0.0
    before = (total * total) / (n_regions * squares) if squares else 0.0
    x = done_by_region.get(region, 0)
    after = ((total + 1) ** 2) / (n_regions * (squares + 2 * x + 1))
    return after - before


def choose_action(candidates, snapshot, memory):
    """挑一个候选观测，或者返回 None 表示这一时隙先等。"""
    if not candidates:
        # 没有能完成的事。等待每秒只扣 0.001，是最便宜的动作——但注意，只要有得拍就别等：
        # 实测「天况不好就主动等」这条规则会让 5 个种子平均掉 1700 分，因为错过的窗口不会回来。
        return None

    now = _utc((snapshot.get("cursor") or {}).get("timestamp_utc")) or 0.0
    chances = remaining_chances(snapshot, now)
    shortfall = region_shortfall(snapshot)
    urgent_requests = expiring_requests(snapshot, now)

    # ① 必做天区告急 —— 漏一块 1000 分，是所有罚分里最重的，优先级最高
    at_risk = [
        (chances.get(c.get("tile_id"), 99), rank, c)
        for rank, c in enumerate(candidates)
        if (c.get("scheduling_class") or "").upper() == "REQUIRED"
        and chances.get(c.get("tile_id"), 99) <= LAST_CHANCES
    ]
    if at_risk:
        at_risk.sort(key=lambda item: (item[0], item[1]))  # 机会最少的先拿，平手时听平台的
        chosen = at_risk[0][2]
        chosen["reason"] = f"required tile with only {at_risk[0][0]} window(s) left"
        return chosen

    # ② 请求快到期 —— 330 分的摆动，比大多数单次曝光的科学分都大。
    #    ⚠️ 默认关闭：在当前这个 5% 订阅率的赛题上实测会掉分，因为插队换来的请求奖励
    #    抵不过让出的那一枪科学分。赛题收紧后这条就该打开。把 if False 改成 if True 试试。
    if False:  # noqa: SIM223 - 留给你自己打开做对照实验
        for candidate in candidates:
            if (candidate.get("request_id") or "") in urgent_requests:
                candidate["reason"] = "observation request expiring within a day"
                return candidate

    # ③ 分区配额告急 —— 这块可选天区快没机会了，而它所在分区还没凑够 4 块。
    #    ⚠️ 同样默认关闭，原因同上。注意有些分区是结构性凑不满的（整片天区这一个月
    #    只在白天过中天），那部分缺额无论如何都拿不回来，别在上面浪费好天。
    if False:  # noqa: SIM223
        for candidate in candidates:
            if (candidate.get("scheduling_class") or "").upper() == "REQUIRED":
                continue
            if shortfall.get(candidate.get("region_id"), 0) <= 0:
                continue
            if chances.get(candidate.get("tile_id"), 99) <= LAST_CHANCES:
                candidate["reason"] = "last chance at a region still short of its flexible quota"
                return candidate

    # ④ 覆盖均匀性 —— 正式比赛场景里这一项占总分约两成，是最值得算的一笔。
    #
    #    coverage_bonus = W · base_science · E，E 是已完成天区在 8 个分区间的均匀度
    #    （Jain 公平指数）。它的意思是：只盯着好拍的分区猛拍，E 会掉，掉的是整场科学分的一个比例，
    #    远比单枪的科学分值钱。所以这里算的是「把这一块拍了，E 能涨多少」，再折算回分数。
    #
    #    W 从场景配置读；练习场景是 0，这条自动失效，所以本地练习不会被带偏。
    weight = coverage_weight(snapshot)
    if weight > 0.0:
        done_by_region = memory.setdefault("_coverage", {})
        science_so_far = float(memory.get("_science", 0.0))
        n_regions = max(1, len(done_by_region) or 8)
        best = None
        best_value = float("-inf")
        for candidate in candidates:
            seconds = max(1.0, float(candidate.get("nominal_exptime_seconds") or 900))
            value = float(candidate.get("estimated_total_gain") or 0.0)
            value += weight * science_so_far * _evenness_gain(done_by_region, candidate.get("region_id"), n_regions)
            value /= seconds
            if value > best_value:
                best_value, best = value, candidate
        if best is not None:
            region = best.get("region_id")
            done_by_region[region] = done_by_region.get(region, 0) + 1
            memory["_science"] = science_so_far + float(best.get("estimated_science_score") or 0.0)
            best["reason"] = "immediate gain plus what it does to coverage evenness"
            return best

    # ⑤ 没有任何一笔未来的账告急，就信任平台按「每秒收益」排好的第一名。
    #    它已经把大气质量、月光折减、科学权重和项目加成都算进去了，不要再乱加权重。
    best = candidates[0]
    best["reason"] = "platform ranking: highest estimated gain per second"
    return best
