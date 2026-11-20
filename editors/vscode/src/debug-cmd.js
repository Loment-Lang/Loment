// debug-cmd.js — 怎么起调试适配器 (纯 Node, 无 vscode 依赖)
//
// 单独成文件，和 server-path.js / build-cmd.js 一个道理: 这一环最容易错、又最不需要
// GUI 才能验。判据 (tools/vscode_ext_test.py) 无头跑它，把每条分支钉住。

'use strict';

const fs = require('fs');
const path = require('path');

/** 从 startDir 逐级向上找 <dir>/tools/loment_dap.py, 最多 8 层。找不到返回 null。 */
function findDap(startDir, maxUp) {
  const limit = typeof maxUp === 'number' ? maxUp : 8;
  let cur = startDir ? path.resolve(startDir) : null;
  for (let i = 0; i < limit && cur; i += 1) {
    const cand = path.join(cur, 'tools', 'loment_dap.py');
    if (fs.existsSync(cand)) {
      return cand;
    }
    const up = path.dirname(cur);
    if (up === cur) {
      break;
    }
    cur = up;
  }
  return null;
}

/**
 * Windows 路径 -> WSL 的 /mnt/<盘>/... 形式。
 *
 * 适配器跑在 WSL 里，而 VS Code 给的是 Windows 路径 —— 适配器自己也做这一步
 * (`loment_dap._norm`)，这里做是因为**要拿它去执行** (`wsl -e python3 <这个>`):
 * `wsl.exe` 认 Windows 路径，WSL 里的 python 只认 `/mnt/...`。
 */
function wslPath(p) {
  const s = String(p).replace(/\\/g, '/');
  const m = /^([A-Za-z]):\/(.*)$/.exec(s);
  return m ? `/mnt/${m[1].toLowerCase()}/${m[2]}` : s;
}

/**
 * 调试适配器的调用方式。返回 `{cmd, args, how}` 或 `{error}`。
 *
 * 三层，和 build-cmd.js 一样"每一层都能用，一层都没有就说清楚":
 *   ① 配了 loment.dapCommand -> 直接用它 (自己实现的适配器、或别的语言重写的);
 *   ② Windows -> `wsl -e python3 <仓库里的 tools/loment_dap.py>`。
 *      **必须走 WSL**: 后端是 ptrace，Windows 上没有;
 *   ③ 非 Windows -> `python3 <tools/loment_dap.py>`。
 *
 * `{error}` 必须弹给用户 —— 这一格的失败模式是"按了 F5 没反应"。
 */
function invocation(o) {
  const opts = o || {};
  const cmd = (opts.dapCommand || '').trim();
  if (cmd) {
    const args = Array.isArray(opts.dapArgs) ? opts.dapArgs.map(String) : [];
    return { cmd, args, how: 'configured loment.dapCommand' };
  }
  const script = ((opts.dapScript || '').trim()
                  || findDap(opts.root)
                  || findDap(opts.fileDir));
  if (!script) {
    return { error: 'Cannot find tools/loment_dap.py (set loment.dapScript to an absolute path,'
                    + ' or use loment.dapCommand/dapArgs)' };
  }
  if (opts.platform === 'win32') {
    return { cmd: 'wsl', args: ['-e', 'python3', wslPath(script)], how: 'WSL + python3' };
  }
  return { cmd: 'python3', args: [script], how: 'python3' };
}

module.exports = { invocation, findDap, wslPath };
