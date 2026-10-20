/*
 * loment-restart — LMS 第 6 级（`docs/253` §5.5，issue #390）。
 *
 * 一个**用 C 写的**重启器：跑一个程序；它死了就把它拉起来。
 *
 * 为什么是 C，而不是 Loment（三条，第二条是全部理由）：
 *
 *   1. C 在本仓已经是**另一条实现线**（`tools/lctrans.py` 那门表层语法），所以这不是
 *      引入一个新东西；
 *   2. **它必须不依赖它要重启的那个东西。** 一个用 Loment 写的重启器，在 Loment 崩了
 *      之后还能不能跑，是**它自己要回答的问题** —— 循环。写成 C 就把这个循环切断；
 *   3. 前史：MINIX 3 的 **reincarnation server**（Herder 等，EuroSys'06 / DSN'07）——
 *      驱动是用户态进程，被 kill 之后由 RS 起回来。
 *
 * ⚠ **它不是信任地板，只是一半。** 准确地说是「**重启器 + 最小子集 + 编它们的那些
 * 工具链**」—— 单独一个 C 重启器回答不了 Thompson 那道题（编它的那个 C 编译器又怎么信）。
 * 这一句必须跟着这个文件走，否则它就是一块**没被标注的地板**（`docs/253` §1.2）。
 *
 * 语义，**全部写死在这里**：
 *   * 退出码 0 ⇒ 不重启，原样返回 0。正常结束不是崩溃。
 *   * 非 0 退出 / 被信号杀死 ⇒ 重启，并往 stderr 写一行 `restart <n> after <status>`。
 *   * **最多重启 `LOMENT_RESTART_MAX` 次**（默认 5）。崩到第 6 次就**停**并把最后一次的
 *     状态返回 —— 一个永远重启的守护进程会把一次真崩溃变成一条看不见的日志。
 *   * 每次重启之间睡 `LOMENT_RESTART_DELAY_MS`（默认 200 ms），让"启动即崩"不至于烧满 CPU。
 *
 * 不做的事（明写，免得被读成承诺）：不做进程组、不做多进程、不做状态检查点 ——
 * "退出所有 Loment 进程"在 `no_std` 下**表达不出来**（`docs/253` §5.5 那条硬边界），
 * 这里能做到的是**自己退出、让 OS 收尸、由父进程拉起**。
 *
 * 编译: cc -O2 -o loment-restart loment-restart.c
 * 用法: ./loment-restart <program> [args...]
 */

#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef LOMENT_RESTART_MAX
#define LOMENT_RESTART_MAX 5
#endif

#ifndef LOMENT_RESTART_DELAY_MS
#define LOMENT_RESTART_DELAY_MS 200
#endif

static void nap_ms(long ms) {
    struct timespec ts;
    ts.tv_sec = ms / 1000;
    ts.tv_nsec = (ms % 1000) * 1000000L;
    while (nanosleep(&ts, &ts) == -1 && errno == EINTR) {
        /* 被信号打断就把剩下的睡完 —— 这里不丢唤醒。 */
    }
}

/* 跑一次，返回 (退出码 | 128+信号)；起不来返回 127。 */
static int run_once(char **argv) {
    pid_t pid = fork();
    if (pid < 0) {
        fprintf(stderr, "loment-restart: fork: %s\n", strerror(errno));
        return 127;
    }
    if (pid == 0) {
        execvp(argv[0], argv);
        fprintf(stderr, "loment-restart: exec %s: %s\n", argv[0], strerror(errno));
        _exit(127);
    }
    int status = 0;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR) {
            fprintf(stderr, "loment-restart: waitpid: %s\n", strerror(errno));
            return 127;
        }
    }
    if (WIFSIGNALED(status)) {
        return 128 + WTERMSIG(status);
    }
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    return 127;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <program> [args...]\n", argv[0]);
        return 2;
    }
    int restarts = 0;
    for (;;) {
        int rc = run_once(&argv[1]);
        if (rc == 0) {
            return 0;
        }
        if (restarts >= LOMENT_RESTART_MAX) {
            fprintf(stderr, "loment-restart: giving up after %d restarts (last status %d)\n",
                    restarts, rc);
            return rc;
        }
        restarts += 1;
        fprintf(stderr, "loment-restart: restart %d after %d\n", restarts, rc);
        fflush(stderr);
        nap_ms(LOMENT_RESTART_DELAY_MS);
    }
}
