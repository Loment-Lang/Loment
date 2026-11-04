// grammar-detect.js — 读一份 L1 源**头部**那一行 `choose write grammar <别名>`（docs/188），
// 定出这份源该按**哪门表层语法**读、上色。
//
// ## 为什么在这里另写一份
//
// 参考实现在 `tools/potato_from.py`（`find_decl` / `read_grammar_decl`），但扩展**不能起
// Python** —— Windows 上多半没装，而且这是 VS Code 每次开文件都要跑的东西。
//
// 于是这一份是"**同一套规矩的第二份表达**"。与 `tools/lomentc.py` ↔ `loment/selfhost/`
// 那条孪生纪律一样，两份必须说同一件事：`tools/vscode_ext_test.py` 的
// `test_vscode_grammar_decl_matches_reference` 把同一批样例喂给**两边**、
// 逐条比 `(规范名, 声明成立吗)` —— 只比这一对，不比报错文案（文案归编译器）。
//
// **没写 = 原生**（`loment`）。声明写坏了也退回原生 —— 与参考一致：它同样返回
// `("loment", <报错>, False)`，由编译器把报错说给用户听，编辑器这一侧只负责"别乱上色"。

'use strict';

// 出厂锁的取值表（源里写 -> 规范名）。**与 tools/potato_from.py 的 `GRAMMAR_ALIASES` 同表**,
// 判据逐条对拍。源侧宽松（`py` / `python` 都收）、对象侧只许一个拼法（docs/188 §1.2）。
const GRAMMAR_ALIASES = {
  loment: 'loment',
  tur: 'tur',
  // Tur 2 是**版次名**，不是第四门语法（docs/296）：同一条原生读法，多一个 profile。
  tur2: 'tur2',
  c: 'c',
  py: 'python', python: 'python',
  java: 'java',
  cs: 'csharp', csharp: 'csharp', 'c#': 'csharp',
  cpp: 'cpp', 'c++': 'cpp', cxx: 'cpp', cc: 'cpp',
  go: 'go', golang: 'go',
  rs: 'rust', rust: 'rust',
  nl: 'natural', natural: 'natural', lument: 'natural',
};

//: 读法就是原生语法的规范名 —— 它们**不需要**另立一份语法文件：
//: `loment` 是默认拼法，`tur` 是本语言自己的正式语法名（2026-11-03，`docs/295`），
//: `tur2` 是它的第二个版次（2026-11-04，`docs/296`）—— 上色用的是同一套。
//: **`rust` 不在这里**：它退回成一门表层语法（与 C/Java/Go 同级），所以它有
//: `loment-rust` 那一套语法文件。
const NATIVE_GRAMMARS = ['loment', 'tur', 'tur2'];

//: 声明里那三个词 —— **与参考实现同一份词序契约**（`GRAMMAR_DECL_WORDS`）。
const GRAMMAR_DECL_WORDS = ['choose', 'write', 'grammar'];

//: 行首那一格**允许一个 UTF-8 BOM**：Windows 的编辑器默认会写 BOM，而 BOM 不是空白 ——
//: 不认它，整条声明就找不到、文件被当原生编（参考实现为此专门改过，见 `_GRAMMAR_LEAD`）。
const LEAD = '^(?:\\ufeff)?[ \\t]*';
const HEAD = GRAMMAR_DECL_WORDS.join('[ \\t]+');
//: 三个词之后**只认空白 / `;` / 行尾** —— 显式集合，不是 `\b`：`grammar#python` 不算声明头。
const TAIL = '(?=[ \\t;]|$)';

const ANY = new RegExp(LEAD + HEAD + TAIL, 'gm');
//: 注意那把 `ym` —— `^` 要按**行首**解，而这一条是拿 `lastIndex` 定在某一行的开头去试的。
//: 少了 `m` 时 `^` 只认**整串**开头，于是"声明不写在第 1 行"的源（如
//: `loment/examples/cproject/bitkit.lomt`：前面一段块注释、第 12 行才是声明）一律读不到 ——
//: 而手挑的那几条边角正好都在第 1 行，全绿。是**枚举全仓语料**把它抓出来的。
const DECL = new RegExp(LEAD + HEAD + TAIL + '[ \\t]+([^\\s;]+)', 'ym');
const MODULE_LINE = /^[ \t]*module[ \t]+[A-Za-z_]\w*/m;

function lineOf(text, index) {
  let n = 1;
  for (let i = 0; i < index; i += 1) {
    if (text[i] === '\n') {
      n += 1;
    }
  }
  return n;
}

/**
 * **只"找"**那一行 —— 与参考实现的 `find_decl` 同一个职责（那一层与报错器共享）。
 * 不管"必须在 `module` 之前"、"只许写一次"、别名在不在出厂锁里。
 *
 * @returns {{found: boolean, alias: string|null, line: number, hits: number, at: number}}
 */
function findDecl(text) {
  const hits = [];
  ANY.lastIndex = 0;
  let m = ANY.exec(text);
  while (m !== null) {
    hits.push(m.index);
    if (m.index === ANY.lastIndex) {
      ANY.lastIndex += 1;                       // 零宽匹配也往前走，别转圈
    }
    m = ANY.exec(text);
  }
  if (!hits.length) {
    return { found: false, alias: null, line: 0, hits: 0, at: -1 };
  }
  const at = hits[0];
  DECL.lastIndex = at;
  const d = DECL.exec(text);
  return {
    found: true,
    alias: d ? d[1] : null,
    line: lineOf(text, at),
    hits: hits.length,
    at,
  };
}

/**
 * 文件头预扫：`choose write grammar <别名>` -> 这份源该按哪门规范语法读。
 *
 * 三条规矩与参考实现逐条对应（`read_grammar_decl`）：必须在 `module` 之前、只许写一次、
 * 别名要在出厂锁的取值表里。**任何一条不成立都退回 `loment`** —— 报错由编译器说。
 *
 * @returns {{grammar: string, declared: boolean, problem: string|null}}
 */
function readGrammarDecl(text) {
  const f = findDecl(text);
  if (!f.found) {
    return { grammar: 'loment', declared: false, problem: null };
  }
  const mod = MODULE_LINE.exec(text);
  if (mod !== null && mod.index < f.at) {
    return { grammar: 'loment', declared: false, problem: 'after-module' };
  }
  if (f.hits > 1) {
    return { grammar: 'loment', declared: false, problem: 'declared-twice' };
  }
  if (f.alias === null) {
    return { grammar: 'loment', declared: false, problem: 'no-alias' };
  }
  const word = f.alias.trim().toLowerCase();
  if (!Object.prototype.hasOwnProperty.call(GRAMMAR_ALIASES, word)) {
    return { grammar: 'loment', declared: false, problem: 'unknown-alias' };
  }
  return { grammar: GRAMMAR_ALIASES[word], declared: true, problem: null };
}

/** 规范名 -> VS Code 语言 id。原生那几个仍归 `loment`，其余的各有 `loment-<规范名>`。 */
function languageIdFor(grammar) {
  return NATIVE_GRAMMARS.indexOf(grammar) >= 0 ? 'loment' : `loment-${grammar}`;
}

module.exports = { GRAMMAR_ALIASES, NATIVE_GRAMMARS, GRAMMAR_DECL_WORDS, findDecl,
                   readGrammarDecl, languageIdFor };
