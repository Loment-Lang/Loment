// conf-ext.js — 读项目自己那份 `loment.conf` 的 `source_ext()`，也就是**这个项目的 L1
// 源用什么后缀**（默认 `.lomt`，见 docs/149 §7.1 / `tools/lomentc.py` 的 `source_ext_of`）。
//
// 配置就是**一份 Loment 源码**，只认一个标签：
//
//     pub fn source_ext() -> str { return ".foo"; }
//
// ## 为什么在这里另写一份
//
// 参考实现在 `tools/lomentc.py`（`_conf_ext_from_tokens`），扩展不能起 Python。
// 规则**逐条**照抄：**词法器**扫（于是注释里的 `source_ext` 或字符串**不命中**）、
// 取那个标签之后**第一个字符串字面量**、必须以 `.` 开头、不许带转义 —— 任一条不成立就当
// **没配**（配置文件坏掉不该让编辑器炸，与编译器同一条处置：读不出来就退回默认）。
//
// **一处刻意记下的分歧**：带转义的字符串这边**直接当没配**，参考实现是"先解转义、再判"。
// 分叉只可能出现在"后缀里带转义"这种离谱输入上（正常后缀是 `.foo`），
// 对拍语料不含它 —— 记在这里而不是假装没有。

'use strict';

const fs = require('fs');
const path = require('path');

const DEFAULT_L1_EXT = '.lomt';
const CONF_NAME = 'loment.conf';

/** 极简词法器：够读一份 conf 的标签与字符串；行注释与块注释都不进流。 */
function lex(text) {
  const toks = [];
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === '/' && text[i + 1] === '/') {
      while (i < text.length && text[i] !== '\n') {
        i += 1;
      }
    } else if (c === '/' && text[i + 1] === '*') {
      i += 2;
      while (i < text.length && !(text[i] === '*' && text[i + 1] === '/')) {
        i += 1;
      }
      i += 2;
    } else if (c === '"') {
      let j = i + 1;
      while (j < text.length && text[j] !== '"') {
        j += text[j] === '\\' ? 2 : 1;
      }
      toks.push({ kind: 'string', val: text.slice(i + 1, j) });
      i = j + 1;
    } else if (/[A-Za-z_]/.test(c)) {
      let j = i;
      while (j < text.length && /[A-Za-z0-9_]/.test(text[j])) {
        j += 1;
      }
      toks.push({ kind: 'ident', val: text.slice(i, j) });
      i = j;
    } else {
      i += 1;
    }
  }
  return toks;
}

/**
 * `source_ext` 之后第一个字符串字面量；没有 / 不合规矩 -> `null`（= 没配）。
 * 与 `tools/lomentc.py` 的 `_conf_ext_from_tokens` 同一条规则。
 */
function sourceExtFromTokens(toks) {
  for (let i = 0; i < toks.length; i += 1) {
    if (toks[i].kind !== 'ident' || toks[i].val !== 'source_ext') {
      continue;
    }
    for (let j = i + 1; j < toks.length; j += 1) {
      if (toks[j].kind === 'string') {
        const v = toks[j].val;
        if (!v.startsWith('.') || v.indexOf('\\') >= 0) {
          return null;
        }
        return v;
      }
    }
    return null;                             // 有标签、后面没有字符串 = 没配
  }
  return null;
}

/** 从 conf 的**原文**读后缀；读不出来一律 `null`。 */
function readSourceExt(text) {
  try {
    return sourceExtFromTokens(lex(text));
  } catch (e) {
    return null;
  }
}

/** 逐级向上找 `loment.conf`（与 `server-path.js` 找 `tools/loment_lsp.py` 同一个走法）。 */
function findConf(startDir, maxUp = 8) {
  let dir = startDir;
  for (let i = 0; i <= maxUp && dir; i += 1) {
    const p = path.join(dir, CONF_NAME);
    if (fs.existsSync(p)) {
      return p;
    }
    const up = path.dirname(dir);
    if (up === dir) {
      break;
    }
    dir = up;
  }
  return null;
}

/** 某个目录（项目根）配置的 L1 后缀。没配就是默认的 `.lomt`。 */
function sourceExtForDir(dir) {
  if (!dir) {
    return DEFAULT_L1_EXT;
  }
  const conf = findConf(dir);
  if (!conf) {
    return DEFAULT_L1_EXT;
  }
  try {
    const ext = readSourceExt(fs.readFileSync(conf, 'utf8'));
    return ext || DEFAULT_L1_EXT;
  } catch (e) {
    return DEFAULT_L1_EXT;                   // 读不动 = 没配，不是错误
  }
}

module.exports = { DEFAULT_L1_EXT, CONF_NAME, readSourceExt, findConf, sourceExtForDir };
