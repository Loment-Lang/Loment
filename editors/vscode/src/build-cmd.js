// build-cmd.js — 决定"**怎么编** / **怎么跑**"（纯 Node，无 vscode 依赖）
//
// 单独成文件是为了**可无头测试**（与 `server-path.js` 同一个理由）：选哪条编译路径
// 是扩展里最容易错、又最不需要 GUI 才能验的一环 —— 而它错了的表现是"点了没反应"
// 或者"编出来的是别的东西"，在编辑器里看不出来。
//
// ## 三层检测 —— 为什么是三层
//
// 仓库里**没有**"单个 .lomt 出可执行文件"的 Python CLI（`loment build DIR` 是**整目录
// 增量构建**，且只出 IR）。单文件出可执行是这两条：
//
//   ① **装了 Loment 命令**：`loment build FILE [-o NAME]` / `loment run FILE`
//      （`loment/tools/lomcli.lomt:606`）—— **产品路径，不需要 Python**
//   ② **Windows 开发树**：`scripts/lomc.ps1` —— 种子 + clang + WSL，出 `.ll` + `.elf`
//   ③ 两条都没有 -> **明确报错**，不猜。
//
// ③ 不是兜底失败，是**这一格该有的行为**：本仓的规矩是把"静默做错"换成"报错退出"
// （`docs/167`）。点"编译"什么都没发生、也没说为什么，是最难查的一种。

'use strict';

const fs = require('fs');
const path = require('path');
const { readGrammarDecl, NATIVE_GRAMMARS } = require('./grammar-detect');

/** 相对路径一律用 `/` —— 传给外部命令，反斜杠在 WSL/clang 那侧会被当转义。 */
function posixRel(root, p) {
  return path.relative(root, p).replace(/\\/g, '/');
}

/** `loment build` 的 `-o` **不带扩展名**（`lomcli.lomt` 明说）：`-o hi` 给 `hi.exe`/`hi`。 */
function outName(buildDir, file) {
  const stem = path.basename(file).replace(/\.lomt$/i, '');
  return buildDir ? `${buildDir}/${stem}` : stem;
}

/**
 * 从 startDir 逐级向上找 `<dir>/scripts/lomc.ps1`，最多 8 层。找不到返回 null。
 *
 * **为什么必须向上找**：VS Code 里"直接打开一个文件"（不打开文件夹）是极常见的用法 ——
 * 那时窗口是**空的**，扩展只能拿**文件所在目录**当基准，而 `scripts/lomc.ps1` 在仓库根。
 * 2026-09-18 实测：用户双击打开 `loment/examples/mathutil.lomt`，点运行得到
 * 「找不到能编译的东西」—— 工具链明明在，只是没从那个目录往上看。
 *
 * `server-path.js` 与 `debug-cmd.js` 早就是向上找的，只有这里不是 —— 补齐这一处。
 */
function findWrapper(startDir, maxUp) {
  const limit = typeof maxUp === 'number' ? maxUp : 8;
  let cur = startDir ? path.resolve(startDir) : null;
  for (let i = 0; i < limit && cur; i += 1) {
    const cand = path.join(cur, 'scripts', 'lomc.ps1');
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
 * 这个文件里有没有 `fn _start`（ELF 的入口）。
 *
 * **必须自己有**：2026-09-18 全仓清点过，**没有一例**是"入口来自 `use` 的模块"
 * （`loment` 下所有 `.lomt` 里，凡定义 `_start` 的文件都自己编成程序）。所以这一条能当判据用，
 * 不用去解 `use` 树。
 *
 * 读不了就不拦（返回 true）—— 宁可让它去编，也不假装它是个库。
 */
function hasEntry(file) {
  try {
    return /\bfn\s+_start\b/.test(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    return true;
  }
}

/**
 * 决定用哪条路。返回 `{cmd, args, cwd, how}` 或 `{error}`。
 *
 * @param {object} o
 * @param {string} o.root        工作区根（命令的 cwd 与相对路径的基准）
 * @param {string} o.file        要编的 `.lomt` 的绝对路径
 * @param {'build'|'run'} o.action
 * @param {string} [o.platform]  `process.platform`（测试可注入）
 * @param {string} [o.toolCommand] `loment.toolCommand`
 * @param {string[]} [o.toolArgs]  `loment.toolArgs`
 * @param {string} [o.buildDir]  `loment.buildDir`（相对工作区根）
 */
/** 抹掉注释, 只为了别把注释里的 `use "..."` 当成依赖。换行留着 —— `^` 是按行锚的。 */
function stripComments(text) {
  let out = '';
  let i = 0;
  const n = text.length;
  while (i < n) {
    const c = text[i];
    if (c === '/' && text[i + 1] === '/') {
      while (i < n && text[i] !== '\n') {
        i += 1;
      }
    } else if (c === '/' && text[i + 1] === '*') {
      i += 2;
      while (i < n && !(text[i] === '*' && text[i + 1] === '/')) {
        i += 1;
      }
      i += 2;
    } else if (c === '"') {
      let j = i + 1;
      while (j < n && text[j] !== '"') {
        j += text[j] === '\\' ? 2 : 1;
      }
      out += text.slice(i, Math.min(j + 1, n));
      i = j + 1;
    } else {
      out += c;
      i += 1;
    }
  }
  return out;
}

/**
 * 文件头声明了哪门**非原生**表层语法；没有（或声明的是 `loment`/`rust`）就 null。
 *
 * 声明怎么读与参考实现逐条对拍（`grammar-detect.js` 那一套，`vscode_ext_test` 枚举全仓
 * 语料盯着的就是它）。这里只多问一句"是不是原生"。
 */
function surfaceGrammarOf(file) {
  try {
    const d = readGrammarDecl(fs.readFileSync(file, 'utf8'));
    return d.declared && NATIVE_GRAMMARS.indexOf(d.grammar) < 0 ? d.grammar : null;
  } catch (e) {
    return null;
  }
}

/**
 * 这个**编译单元**里第一份带非原生表层语法声明的文件：入口自己，或入口 `use "…"` 的
 * 同目录模块（一层）。
 *
 * **为什么必须看依赖**：`loment/examples/cproject/main.lomt` 是原生写的宿主，表层语法在
 * 它 `use` 的 `bitkit.lomt` 里 —— 编译失败的是**那个依赖**，而报错说的是"this file"。
 * 用户照着入口文件找半天，找不到任何 `choose write grammar`。
 *
 * **为什么只找一层**：拼接层的 `use` 树由工具链自己解；编辑器这一侧只要够早地认出
 * "这个单元里有外门语法的叶子"，多解一层就多一份会漂的重复实现。漏一层的代价是退回
 * 工具链原来的报错，不是错判。
 */
function surfaceInUnit(file) {
  const own = surfaceGrammarOf(file);
  if (own) {
    return { file, grammar: own, entry: true };
  }
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (e) {
    return null;
  }
  const dir = path.dirname(file);
  const re = /^[ \t]*use[ \t]+"([^"]+)"/gm;
  const src = stripComments(text);
  let m;
  while ((m = re.exec(src)) !== null) {
    const dep = path.resolve(dir, m[1]);
    const g = surfaceGrammarOf(dep);
    if (g) {
      return { file: dep, grammar: g, entry: false };
    }
  }
  return null;
}

/**
 * 这份源有没有 `use`（路径形式 `use "m.lomt"` 或名字形式 `use std`）。
 *
 * 有的话**语言服务的诊断不完整**：两个实现都只查当前文件（`_parse`/`run_check` 都不做
 * `resolve_deps`），于是跨模块的名字全报 `E002 符号未声明` —— 一份编得过的多文件工程
 * 在编辑器里满屏假错（2026-10-21 实测，`cproject/main.lomt` 5 条）。调用方据此挑检查器。
 */
function usesModules(text) {
  return /^[ \t]*use[ \t]+(?:"|[A-Za-z_])/m.test(stripComments(text));
}

function invocation(o) {
  const file = o.file;
  const action = o.action === 'run' ? 'run' : 'build';
  const platform = o.platform || process.platform;
  const tool = (o.toolCommand || '').trim();
  // 没打开文件夹时（空窗口）调用方给的是**文件所在目录** —— 那也是个合法的基准。
  const root = o.root || path.dirname(file);

  // ---- ⓪' 外门语法：**说清楚是哪个文件，以及现在能怎么编**
  //
  // 2026-10-21 用户报"扩展莫名其妙的不可编译 / 显示不是 Loment"：一份**原生**宿主
  // `use` 了 C 写法的叶子，自举编译器会说 "this file does not look like Loment"
  // （0.1.5 的 CLI）或"这份源的自举侧收不了…"（开发树的 seed）—— 两句都没点名**哪个**
  // 文件，而入口文件里连 `choose write grammar` 都没有。先把这句说出来。
  //
  // 两种形状都拦：入口自己是外门（编译一片叶子），或依赖是外门（编译整个工程）。
  // **不判"工具链能不能翻译"** —— 今天所有路径都是自举侧（收不了），而哪天有了翻译器的
  // 孪生，这条就会变成误拦；所以文案里点名"这条路用的是什么"，而不是下永久结论。
  const surface = surfaceInUnit(file);
  if (surface) {
    const where = path.relative(root, surface.file).replace(/\\/g, '/');
    // 命令编的是**入口**（`use` 树由工具链自己解），不是那个叶子文件 —— 编叶子只会得到
    // 它的 IR，用户要的是整个程序。
    const entry = path.relative(root, file).replace(/\\/g, '/');
    const dir = o.buildDir || 'loment/build';
    const stem = path.basename(file).replace(/\.lomt$/i, '');
    return {
      error: `\`${where}\` declares the **${surface.grammar}** surface grammar `
        + `(\`choose write grammar ${surface.grammar}\`)`
        + (surface.entry ? '.\n' : ' -- it is a module this file uses.\n')
        + 'The compiler this button runs takes only `choose write grammar loment` '
        + '(or `rust`); the other spellings wait on the translator\'s Loment twin '
        + '(docs/189 section 4.1). That module is what the compiler means when it '
        + 'answers "this file does not look like Loment".\n'
        + 'If your toolchain has the translator, point `loment.toolCommand` at it. '
        + 'Otherwise build with the reference implementation (a checkout has one):\n'
        + `  python tools/lomentc.py "${entry}" --emit-llvm ${dir}/${stem}.ll `
        + `--emit-potato ${dir}/${stem}.potato.json\n`
        + `  python tools/lomelf.py ${dir}/${stem}.ll -o ${dir}/${stem} --target elf`,
    };
  }

  // ---- ⓪ 「运行」一个没有 `_start` 的文件，是**没有意义**的（也编不出可执行）
  //
  // 实测症状：链接器给一句 `cannot find entry symbol _start; not setting start address`，
  // 然后那个 ELF 一跑就 `exit code 11`（段错误）—— **用户看到的是"跑不动"，而且不知道
  // 为什么**（`lomc.ps1` 自己退出码还是 0）。这里把它变成一句能读懂的话。
  //
  // 例子：`loment/examples/mathutil.lomt` 是个库模块（只有 `pub fn`/`pub const`），
  // 要运行的是**用它的那个入口文件**。**编译**不受影响（出 IR/目标文件是正当的）。
  if (action === 'run' && !hasEntry(file)) {
    return { error: `This file is not a runnable program — it has no \`fn _start\` (the ELF entry).` +
                    `\nIt is most likely a **library module**: what you want to run is the **entry file that uses it**` +
                    ` (the one with its own \`_start\`, e.g. \`tour.lomt\` next to it).` +
                    `\nTo merely compile it (IR / object file), use "Loment: Build current file".` };
  }

  // ---- ① 装了 Loment 命令（产品路径，不需要 Python）
  if (tool) {
    const rel = posixRel(root, file);
    if (!rel || rel.startsWith('..')) {
      // 相对路径要传给外部命令，`..` 会指到别处（clang/WSL 那侧解出来不是这个文件）
      return { error: 'This file is not inside the workspace — build commands are relative to the workspace root.'
                      + ' Add its directory to the workspace, or open the workspace one level above it.' };
    }
    const extra = Array.isArray(o.toolArgs) ? o.toolArgs.map(String) : [];
    const args = extra.concat([action, rel]);
    if (action === 'build') {
      args.push('-o', outName(o.buildDir, file));
    }
    return { cmd: tool, args, cwd: root, how: 'cli' };
  }

  // ---- ② Windows 开发树：scripts/lomc.ps1（种子 + clang + WSL）
  if (platform === 'win32') {
    // **从工作区和文件所在目录两处向上找** —— 见 `findWrapper` 那条注释。
    const wrapper = findWrapper(root) || findWrapper(path.dirname(file));
    if (wrapper) {
      // `<仓库根>/scripts/lomc.ps1` -> `<仓库根>`：**这个**才是 cwd 与相对路径的基准
      // （`lomc.ps1` 的 `-OutDir` 默认 `loment/build` 是相对 cwd 的）。
      const base = path.dirname(path.dirname(wrapper));
      const rel = posixRel(base, file);
      if (rel && !rel.startsWith('..')) {
        const args = ['-NoProfile', '-File', wrapper, rel];
        if (action === 'run') {
          args.push('-Run');          // 用 WSL 跑那个 Linux ELF（程序用 Linux syscall）
        }
        return { cmd: 'powershell', args, cwd: base, how: 'lomc' };
      }
    }
  }

  // ---- ③ 两条都没有：说清楚该做什么，别静默
  return {
    error: 'Nothing to build with. Two ways, pick either:\n'
      + '  · install the toolchain and set `loment.toolCommand`'
      + ' (with the package installed it is `loment`; to run through WSL on Windows set `wsl`,'
      + ' with `loment.toolArgs` = ["-e", "/home/<you>/.local/share/loment/bin/loment"]);\n'
      + '  · or put this file inside a Loment checkout (`scripts/lomc.ps1` is then used automatically —'
      + ' **no workspace folder required**, it is searched upward from the file).',
  };
}

module.exports = { invocation, posixRel, outName, findWrapper, surfaceInUnit,
                   surfaceGrammarOf, stripComments, usesModules };
