// 全知排程的精确空闲填补 DP（旧版计分），供局部搜索调用。
// 编译：gcc -O3 -march=native -shared -fPIC -o dp.so dp.c
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define INF 1e30

typedef struct {
    int n_ticks, per, n_tiles, n_req;
    const uint64_t *act;      // [n_ticks] 该格能完成（_can_complete_from）的天区
    const int8_t *outcome;    // [n_tiles*n_ticks]
    const int32_t *endt;      // [n_tiles*n_ticks]
    const double *value;      // [n_tiles*n_ticks] 完成时的最优得分，不能完成为 -INF
    const int32_t *rv_ptr;    // [n_ticks+1] 请求重访选项（天区, 请求），结果为完成或天气中断
    const int32_t *rv_tile;
    const int32_t *rv_req;
    const int32_t *dm_ptr;    // [n_ticks+1] 注定中断选项
    const int32_t *dm_tile;
    const int32_t *req_lo, *req_hi, *req_need;
    const uint64_t *req_mask;
    const double *req_reward, *req_pen;
    const int8_t *req_excused;  // 缺失时是否豁免（场景固定）
    const int8_t *required;     // [n_tiles]
    const int32_t *region;      // [n_tiles]
    int n_regions, flex_quota;
    double rate;                // 每格等待罚分
    double required_pen, flex_pen;
} Problem;

typedef struct {
    double *B, *F;
    int32_t *choice_kind, *choice_a, *choice_b, *choice_end;
    int32_t *fixed_kind, *fixed_tile, *fixed_req;  // 每格的固定动作：0 无，1 科学，2 强制重访
    int32_t *next_fixed;
    uint8_t *blocked;
    uint64_t *inc;
    int32_t *end_of;
    int32_t *forced_tick, *forced_tile, *forced_req;
    int n_forced;
} Work;

static Work *work_new(const Problem *P) {
    Work *W = (Work *)calloc(1, sizeof(Work));
    int n = P->n_ticks + 1;
    W->B = (double *)malloc(sizeof(double) * n);
    W->F = (double *)malloc(sizeof(double) * n);
    W->choice_kind = (int32_t *)malloc(sizeof(int32_t) * n);
    W->choice_a = (int32_t *)malloc(sizeof(int32_t) * n);
    W->choice_b = (int32_t *)malloc(sizeof(int32_t) * n);
    W->choice_end = (int32_t *)malloc(sizeof(int32_t) * n);
    W->fixed_kind = (int32_t *)calloc(n, sizeof(int32_t));
    W->fixed_tile = (int32_t *)malloc(sizeof(int32_t) * n);
    W->fixed_req = (int32_t *)malloc(sizeof(int32_t) * n);
    W->next_fixed = (int32_t *)malloc(sizeof(int32_t) * n);
    W->blocked = (uint8_t *)calloc(n, 1);
    W->inc = (uint64_t *)malloc(sizeof(uint64_t) * n);
    W->end_of = (int32_t *)malloc(sizeof(int32_t) * 64);
    W->forced_tick = (int32_t *)malloc(sizeof(int32_t) * 256);
    W->forced_tile = (int32_t *)malloc(sizeof(int32_t) * 256);
    W->forced_req = (int32_t *)malloc(sizeof(int32_t) * 256);
    return W;
}

void *dp_work_new(const Problem *P) { return work_new(P); }

void dp_work_free(void *ptr) {
    Work *W = (Work *)ptr;
    free(W->B); free(W->F); free(W->choice_kind); free(W->choice_a); free(W->choice_b); free(W->choice_end);
    free(W->fixed_kind); free(W->fixed_tile); free(W->fixed_req); free(W->next_fixed); free(W->blocked); free(W->inc);
    free(W->end_of); free(W->forced_tick); free(W->forced_tile); free(W->forced_req); free(W);
}

// 设置固定动作；返回 0 表示有重叠
static int setup(const Problem *P, Work *W, const int32_t *sci) {
    int n = P->n_ticks;
    memset(W->fixed_kind, 0, sizeof(int32_t) * (n + 1));
    memset(W->blocked, 0, n + 1);
    uint64_t sched = 0;
    for (int i = 0; i < P->n_tiles; i++) {
        W->end_of[i] = n;
        int k = sci[i];
        if (k < 0) continue;
        if (P->outcome[(size_t)i * n + k] != 2) return 0;
        int e = P->endt[(size_t)i * n + k];
        W->end_of[i] = e;
        sched |= 1ULL << i;
    }
    // 科学观测
    for (int i = 0; i < P->n_tiles; i++) {
        int k = sci[i];
        if (k < 0) continue;
        int e = W->end_of[i];
        if (W->fixed_kind[k] || W->blocked[k]) return 0;
        for (int t = k + 1; t < e; t++) {
            if (W->fixed_kind[t] || W->blocked[t]) return 0;
        }
        W->fixed_kind[k] = 1;
        W->fixed_tile[k] = i;
        W->fixed_req[k] = -1;
        for (int t = k + 1; t < e; t++) W->blocked[t] = 1;
    }
    for (int f = 0; f < W->n_forced; f++) {
        int k = W->forced_tick[f], j = W->forced_tile[f];
        int e = P->endt[(size_t)j * n + k];
        if (W->fixed_kind[k] || W->blocked[k]) return 0;
        for (int t = k + 1; t < e; t++)
            if (W->fixed_kind[t] || W->blocked[t]) return 0;
        W->fixed_kind[k] = 2;
        W->fixed_tile[k] = j;
        W->fixed_req[k] = W->forced_req[f];
        for (int t = k + 1; t < e; t++) W->blocked[t] = 1;
    }
    int nxt = n;
    W->next_fixed[n] = n;
    for (int k = n - 1; k >= 0; k--) {
        if (W->fixed_kind[k]) nxt = k;
        W->next_fixed[k] = nxt;
    }
    // inc[k]：格 k 时未完成的天区
    uint64_t inc = 0;
    for (int i = 0; i < P->n_tiles; i++)
        if (W->end_of[i] >= n) inc |= 1ULL << i;
    // 按 end_of 分桶
    static int32_t order[64];
    int m = 0;
    for (int i = 0; i < P->n_tiles; i++)
        if (W->end_of[i] < n) order[m++] = i;
    // 简单插入排序（按 end_of 降序）
    for (int a = 1; a < m; a++) {
        int x = order[a], b = a - 1;
        while (b >= 0 && W->end_of[order[b]] < W->end_of[x]) { order[b + 1] = order[b]; b--; }
        order[b + 1] = x;
    }
    int p = 0;
    W->inc[n] = inc;
    for (int k = n - 1; k >= 0; k--) {
        while (p < m && W->end_of[order[p]] > k) { inc |= 1ULL << order[p]; p++; }
        W->inc[k] = inc;
    }
    (void)sched;
    return 1;
}

static void backward(const Problem *P, Work *W) {
    int n = P->n_ticks, per = P->per;
    double *B = W->B;
    B[n] = 0.0;
    for (int k = n - 1; k >= 0; k--) {
        if (W->blocked[k]) { B[k] = INF; continue; }
        if (W->fixed_kind[k]) {
            int j = W->fixed_tile[k];
            int e = P->endt[(size_t)j * n + k];
            B[k] = B[e];
            W->choice_kind[k] = W->fixed_kind[k];
            W->choice_a[k] = j;
            W->choice_b[k] = W->fixed_req[k];
            W->choice_end[k] = e;
            continue;
        }
        int limit = W->next_fixed[k];
        double best = INF;
        int kind = -1, a = -1, b = -1, en = -1;
        int bnd = (k / per + 1) * per;
        uint64_t inc = W->inc[k];
        if (bnd <= limit) {
            double c = B[bnd] + ((P->act[k] & inc) ? P->rate * (bnd - k) : 0.0);
            if (c < best) { best = c; kind = 0; en = bnd; }
        }
        for (int q = P->rv_ptr[k]; q < P->rv_ptr[k + 1]; q++) {
            int j = P->rv_tile[q];
            if ((inc >> j) & 1ULL) continue;
            int e = P->endt[(size_t)j * n + k];
            if (e > limit) continue;
            if (B[e] < best) { best = B[e]; kind = 3; a = j; b = P->rv_req[q]; en = e; }
        }
        for (int q = P->dm_ptr[k]; q < P->dm_ptr[k + 1]; q++) {
            int i = P->dm_tile[q];
            if (!((inc >> i) & 1ULL)) continue;
            int e = P->endt[(size_t)i * n + k];
            if (e > limit) continue;
            if (B[e] < best) { best = B[e]; kind = 4; a = i; en = e; }
        }
        B[k] = best;
        W->choice_kind[k] = kind;
        W->choice_a[k] = a;
        W->choice_b[k] = b;
        W->choice_end[k] = en;
    }
}

static void forward(const Problem *P, Work *W) {
    int n = P->n_ticks, per = P->per;
    double *F = W->F;
    for (int k = 0; k <= n; k++) F[k] = INF;
    F[0] = 0.0;
    for (int k = 0; k < n; k++) {
        double f = F[k];
        if (f >= INF || W->blocked[k]) continue;
        if (W->fixed_kind[k]) {
            int j = W->fixed_tile[k];
            int e = P->endt[(size_t)j * n + k];
            if (f < F[e]) F[e] = f;
            continue;
        }
        int limit = W->next_fixed[k];
        int bnd = (k / per + 1) * per;
        uint64_t inc = W->inc[k];
        if (bnd <= limit) {
            double c = f + ((P->act[k] & inc) ? P->rate * (bnd - k) : 0.0);
            if (c < F[bnd]) F[bnd] = c;
        }
        for (int q = P->rv_ptr[k]; q < P->rv_ptr[k + 1]; q++) {
            int j = P->rv_tile[q];
            if ((inc >> j) & 1ULL) continue;
            int e = P->endt[(size_t)j * n + k];
            if (e > limit) continue;
            if (f < F[e]) F[e] = f;
        }
        for (int q = P->dm_ptr[k]; q < P->dm_ptr[k + 1]; q++) {
            int i = P->dm_tile[q];
            if (!((inc >> i) & 1ULL)) continue;
            int e = P->endt[(size_t)i * n + k];
            if (e > limit) continue;
            if (f < F[e]) F[e] = f;
        }
    }
}

// 沿最优路径分配请求标签（与 cdp.plan 相同）：按时间顺序，每次完成的观测给一个“还缺这块天区”的请求，
// 优先截止最早的；visited[r] 为已分配给请求 r 的天区掩码
static void collect_visits(const Problem *P, Work *W, uint64_t *visited) {
    int n = P->n_ticks;
    for (int r = 0; r < P->n_req; r++) visited[r] = 0;
    int k = 0;
    while (k < n) {
        int kind = W->choice_kind[k];
        int e = W->choice_end[k];
        if (kind == 1 || kind == 2 || kind == 3) {
            int j = W->choice_a[k];
            if (P->outcome[(size_t)j * n + k] == 2) {
                int pick = -1;
                for (int r = 0; r < P->n_req; r++) {
                    if (k < P->req_lo[r] || k >= P->req_hi[r] || !((P->req_mask[r] >> j) & 1ULL)) continue;
                    if ((visited[r] >> j) & 1ULL) continue;
                    if (__builtin_popcountll(visited[r]) >= P->req_need[r]) continue;
                    if (pick < 0 || P->req_hi[r] < P->req_hi[pick]) pick = r;
                }
                if (pick >= 0) visited[pick] |= 1ULL << j;
            }
        }
        if (e <= k) break;
        k = e;
    }
}

static int popcount64(uint64_t x) { return __builtin_popcountll(x); }

// 评估：sci[i] 为天区 i 的科学观测开始格（-1 为不安排）。返回总分（不可行返回 -INF）。
// info（可空）[0]=罚分 [1]=科学分 [2]=请求奖励 [3]=请求罚 [4]=强制重访数 [5]=缺失请求数
double dp_evaluate(const Problem *P, void *ptr, const int32_t *sci, int max_fix, double *info) {
    Work *W = (Work *)ptr;
    int n = P->n_ticks;
    W->n_forced = 0;
    double science = 0.0;
    int req_missing = 0;
    uint64_t sched = 0;
    for (int i = 0; i < P->n_tiles; i++) {
        if (sci[i] >= 0) {
            double v = P->value[(size_t)i * n + sci[i]];
            if (!(v > -1e29)) return -INF;
            science += v;
            sched |= 1ULL << i;
        } else if (P->required[i]) {
            req_missing++;
        }
    }
    // 灵活天区缺额
    int flex[64] = {0};
    for (int i = 0; i < P->n_tiles; i++)
        if (sci[i] >= 0 && !P->required[i]) flex[P->region[i]]++;
    int shortfall = 0;
    for (int g = 0; g < P->n_regions; g++)
        if (flex[g] < P->flex_quota) shortfall += P->flex_quota - flex[g];
    uint64_t visited[64];
    double rreward = 0, rpen = 0;
    uint64_t hopeless = 0;  // 找不到可插入重访的请求
    for (int round = 0;; round++) {
        if (!setup(P, W, sci)) return -INF;
        backward(P, W);
        if (W->B[0] >= INF) return -INF;
        collect_visits(P, W, visited);
        // 第一个未满足、且还没判定无望的请求
        int worst = -1;
        for (int r = 0; r < P->n_req; r++) {
            if (popcount64(visited[r]) < P->req_need[r] && !((hopeless >> r) & 1ULL)) { worst = r; break; }
        }
        if (worst < 0 || round >= max_fix) break;
        // 最便宜的插入：F[k] + B[e]
        forward(P, W);
        int r = worst;
        double best = INF;
        int bk = -1, bj = -1;
        for (int k = P->req_lo[r]; k < P->req_hi[r]; k++) {
            if (W->blocked[k] || W->fixed_kind[k] || W->F[k] >= INF) continue;
            int limit = W->next_fixed[k];
            uint64_t inc = W->inc[k];
            for (int q = P->rv_ptr[k]; q < P->rv_ptr[k + 1]; q++) {
                if (P->rv_req[q] != r) continue;
                int j = P->rv_tile[q];
                if ((visited[r] >> j) & 1ULL) continue;
                if ((inc >> j) & 1ULL) continue;
                if (P->outcome[(size_t)j * n + k] != 2) continue;
                int e = P->endt[(size_t)j * n + k];
                if (e > limit) continue;
                double c = W->F[k] + W->B[e];
                if (c < best) { best = c; bk = k; bj = j; }
            }
        }
        if (bk < 0) { hopeless |= 1ULL << r; round--; if (round < -64) break; continue; }
        if (W->n_forced >= 256) break;
        W->forced_tick[W->n_forced] = bk;
        W->forced_tile[W->n_forced] = bj;
        W->forced_req[W->n_forced] = r;
        W->n_forced++;
    }
    for (int r = 0; r < P->n_req; r++) {
        if (popcount64(visited[r]) >= P->req_need[r]) rreward += P->req_reward[r];
        else {
            req_missing += 0;
            if (!P->req_excused[r]) rpen += P->req_pen[r];
        }
    }
    int miss = 0;
    for (int r = 0; r < P->n_req; r++)
        if (popcount64(visited[r]) < P->req_need[r]) miss++;
    double penalty = W->B[0];
    double total = science + rreward - rpen - penalty - P->required_pen * req_missing - P->flex_pen * shortfall;
    if (info) {
        info[0] = penalty; info[1] = science; info[2] = rreward; info[3] = rpen; info[4] = W->n_forced; info[5] = miss;
    }
    return total;
}

// 导出最近一次评估的完整路径：kind(1 科学,2 强制重访,3 重访,4 注定中断,0 等待), tick, tile, req
int dp_path(const Problem *P, void *ptr, int32_t *kind, int32_t *tick, int32_t *tile, int32_t *req, int cap) {
    Work *W = (Work *)ptr;
    int n = P->n_ticks, m = 0, k = 0;
    while (k < n && m < cap) {
        int kd = W->choice_kind[k];
        kind[m] = kd; tick[m] = k; tile[m] = W->choice_a[k]; req[m] = W->choice_b[k];
        m++;
        int e = W->choice_end[k];
        if (e <= k) break;
        k = e;
    }
    return m;
}
