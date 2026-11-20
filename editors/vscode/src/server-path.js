// server-path.js — 找 tools/loment_lsp.py (纯 Node, 无 vscode 依赖)
//
// 单独成文件是为了可无头测试: 路径发现是扩展里最容易错、又最不需要 GUI 才能验的一环。

'use strict';

const fs = require('fs');
const path = require('path');

/** 从 startDir 逐级向上找 `<dir>/tools/<name>`, 最多 8 层。找不到返回 null。 */
function findTool(startDir, name, maxUp) {
  const limit = typeof maxUp === 'number' ? maxUp : 8;
  let cur = startDir ? path.resolve(startDir) : null;
  for (let i = 0; i < limit && cur; i += 1) {
    const cand = path.join(cur, 'tools', name);
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

/** 从 startDir 逐级向上找 <dir>/tools/loment_lsp.py, 最多 8 层。找不到返回 null。 */
function findServer(startDir, maxUp) {
  return findTool(startDir, 'loment_lsp.py', maxUp);
}

module.exports = { findServer, findTool };
