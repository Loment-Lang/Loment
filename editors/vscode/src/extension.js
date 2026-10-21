// extension.js — Loment VS Code 扩展 (docs/155 §编辑器集成)
//
// 分工:
//   - 语言服务负责补全 / 跳转 / 诊断 / 格式化 —— 由官方 vscode-languageclient 驱动,
//     因此本文件里没有 child_process, 也不拼任何 shell 命令。
//     两种起法: 默认 `python tools/loment_lsp.py`; 也可以指向 **Loment 版**服务
//     (loment.serverCommand/serverArgs, 例如 Windows 上 `wsl -e <装好的 lsp>`) —— 那样
//     **不需要装 Python**, 见 scripts/install-lsp.ps1 与 docs/157 §3.5。
//   - 构建 / 检查 / 运行 走 VS Code 的任务 (ProcessExecution 传 argv 数组, 不经 shell);
//     "检查" 优先用语言服务的 `--check`(无 Python), "生成 IR" 优先用 scripts/lomc.ps1。

'use strict';

const vscode = require('vscode');
const path = require('path');
const fs = require('fs');
const { LanguageClient } = require('vscode-languageclient/node');
const { findServer, findTool } = require('./server-path');
const { invocation, usesModules } = require('./build-cmd');
const { invocation: debugInvocation } = require('./debug-cmd');
const { readGrammarDecl, languageIdFor } = require('./grammar-detect');
const { DEFAULT_L1_EXT, sourceExtForDir } = require('./conf-ext');

const SERVER_COMMAND = 'python';
const clientName = 'loment';

//: 「Loment 家族」的语言 id：`loment`（原生）与 `loment-<规范名>`（外门表层语法）。
//: **`lom`（L0）不在内** —— 它是另一门语言，命令 / 任务 / 检测都不该罩到它。
const LOMENT_FAMILY = /^loment(-|$)/;

/** @type {LanguageClient | undefined} */
let client;
/** @type {vscode.OutputChannel} */
let output;
/** @type {Map<string, string>} 目录 -> 该项目配置的 L1 后缀（`loment.conf` 的 `source_ext`） */
const extCache = new Map();

function cfg() {
  return vscode.workspace.getConfiguration('loment');
}

/**
 * 扩展的输出通道 —— **必须是 `LogOutputChannel`**（`createOutputChannel(name, {log:true})`）。
 *
 * `vscode-languageclient` **v10** 会给 `outputChannel` 挂 `onDidChangeLogLevel(...)` 并调
 * `outputChannel.error(...)`；普通 `OutputChannel` 这两样都没有，于是语言服务一起来就抛
 *
 *     TypeError: this.outputChannel.error is not a function
 *     TypeError: this.traceOutputChannel.onDidChangeLogLevel is not a function
 *
 * 这个错在升级到 v10 时就有了，只是**扩展当时整个加载不起来**（另一个 bug），所以从没
 * 露过面 —— 修好加载之后第一眼就是它。**只有 `{log:true}` 建的通道带那几个方法。**
 */
function out() {
  if (!output) {
    output = vscode.window.createOutputChannel('Loment', { log: true });
  }
  return output;
}

/**
 * 工作区根: **包含这个文件的那个文件夹**; 单根工作区就是它自己; 没有文件夹就退回文件目录。
 *
 * 原先一律取 `folders[0]` —— 多根工作区里第二、第三个文件夹中的文件于是用**别人的根**
 * 算相对路径，`build-cmd.js` 拿到 `../…` 就报"这个文件不在工作区里"（2026-10-21）。
 */
function workspaceRoot(fallbackDir) {
  const folders = vscode.workspace.workspaceFolders;
  if (folders && folders.length) {
    if (fallbackDir) {
      for (const f of folders) {
        const base = f.uri.fsPath;
        const rel = path.relative(base, fallbackDir);
        if (!rel.startsWith('..') && !path.isAbsolute(rel)) {
          return base;
        }
      }
    }
    return folders[0].uri.fsPath;
  }
  return fallbackDir || undefined;
}

/**
 * 建好 `loment.buildDir`。
 *
 * `loment build FILE -o <buildDir>/<stem>` 的 `-o` 只是**路径**：目录不存在时链接器说的是
 * `lomelf: 写不了输出` —— 用户看不出跟自己有关。本仓里那个目录恰好一直在，所以只有
 * **在别的项目里**才现形（2026-10-21 实测）。失败不拦：真写不了时编译器自己会说。
 */
function ensureBuildDir(root) {
  if (!root) {
    return;
  }
  try {
    fs.mkdirSync(path.resolve(root, cfg().get('buildDir') || 'loment/build'),
                 { recursive: true });
  } catch (e) { /* 让编译器去报 */ }
}

function resolveServerPath() {
  const configured = (cfg().get('serverPath') || '').trim();
  if (configured) {
    return configured;
  }
  const folders = vscode.workspace.workspaceFolders || [];
  for (const f of folders) {
    const hit = findServer(f.uri.fsPath);
    if (hit) {
      return hit;
    }
  }
  const active = vscode.window.activeTextEditor;
  if (active) {
    return findServer(path.dirname(active.document.uri.fsPath));
  }
  return undefined;
}

/**
 * 用户设置里额外列的自定义 L1 后缀（`loment.extraExtensions`）。
 *
 * 为什么要有它：`source_ext` 是从**项目的 `loment.conf`** 读的，而那份文件可能不在
 * 打开的那个文件夹里（打开的是子目录、或项目根在上一层）—— 那时用户可以直接把后缀
 * 写进设置。两处都算了。
 */
function extraExtensions() {
  const v = cfg().get('extraExtensions');
  return (Array.isArray(v) ? v : []).map((s) => String(s).trim().toLowerCase())
                                 .filter(Boolean);
}

/** 某个目录所在项目配置的 L1 后缀（带缓存 —— 每开一个文件都往上走 8 层目录不合适）。 */
function projectExt(dir) {
  if (!extCache.has(dir)) {
    extCache.set(dir, sourceExtForDir(dir));
  }
  return extCache.get(dir);
}

/**
 * 这份文档该用哪个语言 id；`undefined` = **不认领**。
 *
 * 两条来源，**声明优先**（`docs/188` §2 那条分工：**声明**说"这份 Loment 是用哪种写法写的"）：
 *
 *   1. 文件头那一行 `choose write grammar <别名>`（`grammar-detect.js`，与参考实现逐条
 *      对拍）—— 原生写法（`loment` / `rust`）仍归 `loment`，其余七门各有 `loment-<规范名>`；
 *   2. **没写声明**时，看后缀是不是这个项目自己配的 L1 后缀（`loment.conf` 的
 *      `source_ext`，或设置里的 `loment.extraExtensions`）—— 是就按 `loment` 认。
 *
 * 都没命中就不认领：`.lomt` 有 `package.json` 的静态登记，用不着这里再管一遍。
 */
function targetLanguageId(doc) {
  const decl = readGrammarDecl(doc.getText());
  if (decl.declared) {
    return languageIdFor(decl.grammar);
  }
  const ext = path.extname(doc.uri.fsPath).toLowerCase();
  if (!ext || ext === DEFAULT_L1_EXT) {
    // **回 `loment`，不是"不认领"**：删掉声明之后它仍是原生 Loment 源。返回 undefined
    // 会让它一直停在 `loment-<x>`（状态栏写着外门语法、语言服务也不接）—— 2026-10-21。
    return 'loment';
  }
  if (extraExtensions().indexOf(ext) >= 0) {
    return 'loment';
  }
  const proj = projectExt(path.dirname(doc.uri.fsPath)).toLowerCase();
  return proj !== DEFAULT_L1_EXT && proj === ext ? 'loment' : undefined;
}

/** 我们给哪些文档设过语言 —— 设过的同值不再设（见下面那条注释）。 */
const applied = new Map();

/**
 * 按文件头那行声明 / 项目后缀，把文档切到该在的语言 id 上。
 *
 * **`setTextDocumentLanguage` 会触发一次该文档的 close+open**（VS Code 的既定行为），
 * 所以必须防自激：`applied` 记着"这个文档被我们设成了什么"，同值直接返回。
 * 顺带买到一条更好的性质 —— **用户手动改回去之后我们不再去抢**。
 */
async function applyLanguageHint(doc) {
  if (doc.uri.scheme !== 'file') {
    return;
  }
  const want = targetLanguageId(doc);
  if (!want || want === doc.languageId) {
    return;
  }
  // **不抢**用户显式指定的语言：只动 Loment 家族与"没人认领"（plaintext）。
  // `lom`（L0）因此也不会被这里改掉。
  if (doc.languageId !== 'plaintext' && !LOMENT_FAMILY.test(doc.languageId)) {
    return;
  }
  const key = doc.uri.toString();
  if (applied.get(key) === want) {
    return;
  }
  applied.set(key, want);
  try {
    await vscode.languages.setTextDocumentLanguage(doc, want);
  } catch (e) {
    applied.delete(key);
    out().appendLine(`Language switch failed (${want}): ${e}`);
  }
}

/**
 * 订阅"文档开了 / 文件头动过 / 关了"，并给**已经开着**的文档各补一次。
 *
 * 不带 `context` 也能跑（判据就是拿一个假 `vscode` 直接调的），订阅由 `activate` 收集。
 */
function registerLanguageHints(context) {
  const sub = (d) => {
    applyLanguageHint(d);
  };
  context.subscriptions.push(
    vscode.workspace.onDidOpenTextDocument(sub),
    vscode.workspace.onDidChangeTextDocument((e) => {
      // 声明写在文件头，所以只在这几行动过时才重看 —— 不必每敲一个字就重读一份源。
      if (e.contentChanges.some((c) => c.range.start.line <= 2)) {
        sub(e.document);
      }
    }),
    vscode.workspace.onDidCloseTextDocument((d) => {
      applied.delete(d.uri.toString());
    }),
  );
  // `loment.conf` 改了要重看 —— 缓存不清的话后缀会一直是旧的那个。
  const watcher = vscode.workspace.createFileSystemWatcher('**/loment.conf');
  const drop = (uri) => {
    extCache.delete(path.dirname(uri.fsPath));
  };
  watcher.onDidChange(drop);
  watcher.onDidCreate(drop);
  watcher.onDidDelete(drop);
  context.subscriptions.push(watcher);
  for (const d of vscode.workspace.textDocuments || []) {
    applyLanguageHint(d);
  }
}

/** 用 VS Code 任务跑一条 Loment 工具链命令 (argv 数组, 无 shell)。 */
async function runToolTask(name, args, cwd) {
  const exec = new vscode.ProcessExecution(SERVER_COMMAND, args, cwd ? { cwd } : undefined);
  const task = new vscode.Task({ type: 'loment', name }, vscode.TaskScope.Workspace, name,
                               'loment', exec, []);
  task.presentationOptions = { reveal: vscode.TaskRevealKind.Always, panel: vscode.TaskPanelKind.Shared };
  await vscode.tasks.executeTask(task);
}

/**
 * 语言服务**可执行文件**的调用方式 (argv):
 *   * 配了 loment.serverCommand/serverArgs -> 用它 (例如 wsl -e ~/.local/share/loment/lsp);
 *   * 否则 -> python tools/loment_lsp.py。
 * 返回 undefined 表示两种都不可用。`--check FILE` 也可以这样跑 (无需编辑器)。
 */
function serverInvocation(extraArgs) {
  const cmd = (cfg().get('serverCommand') || '').trim();
  if (cmd) {
    const argsCfg = cfg().get('serverArgs') || [];
    return { cmd, args: (Array.isArray(argsCfg) ? argsCfg.map(String) : []).concat(extraArgs) };
  }
  const serverPath = resolveServerPath();
  if (!serverPath) {
    return undefined;
  }
  return { cmd: SERVER_COMMAND, args: [serverPath].concat(extraArgs) };
}

/** 跑一条任意 argv 的命令 (无 shell), 用于"检查/出 IR"这类自带问题匹配器的命令。 */
async function runExec(name, cmd, args, cwd, matcher) {
  const exec = new vscode.ProcessExecution(cmd, args, cwd ? { cwd } : undefined);
  const task = new vscode.Task({ type: 'loment', name }, vscode.TaskScope.Workspace, name,
                               'loment', exec, matcher ? [matcher] : []);
  task.presentationOptions = { reveal: vscode.TaskRevealKind.Always, panel: vscode.TaskPanelKind.Shared };
  await vscode.tasks.executeTask(task);
}

function currentFile() {
  const ed = vscode.window.activeTextEditor;
  if (!ed) {
    vscode.window.showWarningMessage('Loment: open a .lomt file first');
    return undefined;
  }
  return ed.document.uri.fsPath;
}

function toolPath(root, ...rest) {
  return path.join(root, 'tools', ...rest);
}

/**
 * 任务提供者 —— 让「编译 / 编译并运行」出现在 **`Ctrl+Shift+B`** 与
 * 「Tasks: Run Task」里，而不是只能从命令面板点。
 *
 * ## 为什么要有它（而不是只留命令）
 *
 * `Ctrl+Shift+B`（运行生成任务）是 VS Code 里"编译这个项目"的**原生手势** ——
 * 而它只认**注册过的任务**，不认命令。
 *
 * F5 由**调试器**那一格负责（`debuggers` 贡献 + 下面的调试适配器工厂），
 * 任务提供者管不了它 —— 两者是 VS Code 里两个不同的手势，别混。
 */
class LomentTaskProvider {
  provideTasks() {
    const ed = vscode.window.activeTextEditor;
    // 用**家族**判据而不是 `=== 'loment'`：一份 `choose write grammar python` 的源
    // 还是 Loment 程序，`Ctrl+Shift+B` 该照常能编它。
    if (!ed || !LOMENT_FAMILY.test(ed.document.languageId)) {
      return [];
    }
    const file = ed.document.uri.fsPath;
    const root = workspaceRoot(path.dirname(file));
    ensureBuildDir(root);
    const out = [];
    for (const [action, label] of [['build', 'Loment: 编译'], ['run', 'Loment: 编译并运行']]) {
      const inv = invocation({
        root,
        file,
        action,
        platform: process.platform,
        toolCommand: cfg().get('toolCommand'),
        toolArgs: cfg().get('toolArgs'),
        buildDir: cfg().get('buildDir'),
      });
      if (inv.error) {
        continue;                     // 提供不了就不列 —— 点那条命令时会看到原因
      }
      const exec = new vscode.ProcessExecution(inv.cmd, inv.args, { cwd: inv.cwd });
      const t = new vscode.Task({ type: 'loment', action }, vscode.TaskScope.Workspace, label,
                               'loment', exec, []);
      t.presentationOptions = { reveal: vscode.TaskRevealKind.Always,
                                panel: vscode.TaskPanelKind.Shared };
      if (action === 'build') {
        // **默认生成任务** —— 这一条让 `Ctrl+Shift+B` 直接编当前文件
        t.group = vscode.TaskGroup.Build;
      }
      out.push(t);
    }
    return out;
  }

  resolveTask() {
    return undefined;
  }
}

function buildOut(root, fsPath) {
  const stem = path.basename(fsPath, '.lomt');
  const dir = path.join(root, cfg().get('buildDir') || 'loment/build');
  return { dir, ll: path.join(dir, `${stem}.ll`), potato: path.join(dir, `${stem}.potato.json`) };
}

async function startClient(context) {
  // 语言服务有两种起法:
  //   1) Python 版 (默认): command=python, args=[tools/loment_lsp.py] —— 需要装 Python;
  //   2) Loment 版 (推荐, **不需要 Python**): 用 loment.serverCommand/serverArgs 指定,
  //      例如在 Windows 上经 WSL 跑 scripts/install-lsp.ps1 装好的 ELF:
  //        "loment.serverCommand": "wsl",
  //        "loment.serverArgs": ["-e", "/home/<you>/.local/share/loment/lsp"]
  const cmd = (cfg().get('serverCommand') || '').trim();
  const argsCfg = cfg().get('serverArgs') || [];
  let command = SERVER_COMMAND;
  let args = [];
  let label = '';
  if (cmd) {
    command = cmd;
    args = Array.isArray(argsCfg) ? argsCfg.map(String) : [];
    label = `${command} ${args.join(' ')}`;
  } else {
    const serverPath = resolveServerPath();
    if (!serverPath) {
      vscode.window.showWarningMessage(
        'Loment: cannot find tools/loment_lsp.py (set loment.serverPath to an absolute path, '
        + 'or use loment.serverCommand/serverArgs to point at a Loment language server)');
      return undefined;
    }
    args = [serverPath];
    label = serverPath;
  }
  const chan = out();
  const c = new LanguageClient(
    clientName,
    'Loment',
    // v10 的 Executable 是**接口**: 用对象字面量; 默认 stdio 传输
    { run: { command, args }, debug: { command, args } },
    { documentSelector: [{ scheme: 'file', language: 'loment' },
                          // `#351`：`.nl` 由静态登记成为 `loment-natural` —— 它的正文是
                          // natural 写法，语言服务按后缀认出来、只回一条 Info（不报假错）。
                          { scheme: 'file', language: 'loment-natural' }],
      outputChannel: chan });
  context.subscriptions.push(c);
  await c.start();
  chan.appendLine(`Language server started: ${label}`);
  return c;
}

/**
 * 编译（`action='build'`）或编译并运行（`'run'`）当前文件。
 *
 * **选哪条路不在这里判** —— 那是 `build-cmd.js` 的事（纯模块，判据能无头跑它）。
 * 这里只负责：拿配置 -> 问它 -> 把结论交给 VS Code 任务。
 *
 * 它返回 `{error}` 时**必须弹给用户看**：这一格的失败模式就是"点了没反应"，
 * 而那是最难查的一种（`docs/167` 那条"静默是敌人"）。
 */
async function compileCurrent(action) {
  const file = currentFile();
  if (!file) {
    return;
  }
  const r = workspaceRoot(path.dirname(file));
  ensureBuildDir(r);
  const inv = invocation({
    root: r,
    file,
    action,
    platform: process.platform,
    toolCommand: cfg().get('toolCommand'),
    toolArgs: cfg().get('toolArgs'),
    buildDir: cfg().get('buildDir'),
  });
  if (inv.error) {
    await vscode.window.showErrorMessage(`Loment: ${inv.error}`);
    return;
  }
  await runExec(action === 'run' ? 'Loment: 编译并运行' : 'Loment: 编译', inv.cmd, inv.args,
                inv.cwd);
}

function activate(context) {

  context.subscriptions.push(
    vscode.commands.registerCommand('loment.restartServer', async () => {
      if (client) {
        await client.stop();
        client = undefined;
      }
      client = await startClient(context);
    }),

    vscode.commands.registerCommand('loment.showIr', async () => {
      const file = currentFile();
      if (!file) {
        return;
      }
      const r = workspaceRoot(path.dirname(file));
      const rel = path.relative(r, file).replace(/\\/g, '/');
      const out = buildOut(r, file);
      // 优先走 Loment 侧的一键构建 (种子 + clang + WSL, **不需要 Python**)
      const wrapper = path.join(r, 'scripts', 'lomc.ps1');
      if (process.platform === 'win32' && fs.existsSync(wrapper)) {
        await runExec('Loment: 生成 LLVM IR', 'powershell',
                      ['-NoProfile', '-File', wrapper, rel], r);
        try {
          const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(out.ll));
          await vscode.window.showTextDocument(doc, { preview: true });
        } catch (e) { /* 构建失败时输出已在任务面板里 */ }
        return;
      }
      await runToolTask('Loment: 生成 LLVM IR',
                        [toolPath(r, 'lomentc.py'), file, '--emit-llvm', out.ll,
                         '--emit-potato', out.potato], r);
      const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(out.ll));
      await vscode.window.showTextDocument(doc, { preview: true });
    }),

    vscode.commands.registerCommand('loment.checkFile', async () => {
      const file = currentFile();
      if (!file) {
        return;
      }
      const r = workspaceRoot(path.dirname(file));
      const rel = path.relative(r, file).replace(/\\/g, '/');
      // **有 `use` 的时候先问检出里的参考实现**：`tools/loment.py diag` 会解 `use` 树，
      // 两个语言服务**都不会**（只查当前文件），于是任何多文件工程的跨模块调用都报
      // `E002 符号未声明` —— 2026-10-21 实测：`cproject/main.lomt` 在 `--check` 下 5 条
      // E002，参考实现说 `[OK] 无错误`。没有 `use` 的单文件用 `--check`（**不需要 Python**）。
      let uses = false;
      try {
        uses = usesModules(fs.readFileSync(file, 'utf8'));
      } catch (e) { /* 读不了就按单文件走 */ }
      if (uses) {
        const ref = findTool(r, 'loment.py')
          || findTool(path.dirname(file), 'loment.py');
        if (ref) {
          await runToolTask('Loment: 静态检查', [ref, 'diag', file], r);
          return;
        }
      }
      const inv = serverInvocation(['--check', rel]);
      if (inv) {
        await runExec('Loment: 静态检查', inv.cmd, inv.args, r, '$loment');
        return;
      }
      vscode.window.showErrorMessage(
        'Loment: no checker found — this window has neither a Loment checkout '
        + '(`tools/loment.py`) nor a language server (set `loment.serverPath` or '
        + '`loment.serverCommand`).');
    }),

    vscode.commands.registerCommand('loment.runTests', async () => {
      const file = currentFile();
      if (!file) {
        return;
      }
      const r = workspaceRoot(path.dirname(file));
      await runToolTask('Loment: 运行 test_*', [toolPath(r, 'loment.py'), 'test', file], r);
    }),

    vscode.commands.registerCommand('loment.runInFujoOS', async () => {
      const file = currentFile();
      if (!file) {
        return;
      }
      const r = workspaceRoot(path.dirname(file));
      await runToolTask('Loment: 在 FujoOS 里运行', [toolPath(r, 'loment_boot.py'), file], r);
    }),

    vscode.commands.registerCommand('loment.build', () => compileCurrent('build')),
    vscode.commands.registerCommand('loment.run', () => compileCurrent('run')),

    vscode.commands.registerCommand('loment.formatDocument',
                                    () => vscode.commands.executeCommand('editor.action.formatDocument')));

  const root = workspaceRoot();
  if (root) {
    out().appendLine(`Loment 扩展已激活 (工作区根: ${root})`);
  }

  // 任务提供者: 让 `Ctrl+Shift+B` 与「Tasks: Run Task」看得到「编译 / 编译并运行」
  context.subscriptions.push(
    vscode.tasks.registerTaskProvider('loment', new LomentTaskProvider()));

  // 表层语法: 按文件头那行 `choose write grammar <别名>` 给文档换语言 id
  // (七门外门语法各有 `loment-<规范名>` 一套语法, 见 package.json 的 `grammars`)
  registerLanguageHints(context);

  // 调试适配器: 让 F5 真的开始调试，而不是弹"没有用于调试 Loment 的扩展"。
  // **选哪条命令不在这里判** —— 那是 debug-cmd.js 的事（纯模块，判据无头跑它）。
  context.subscriptions.push(
    vscode.debug.registerDebugAdapterDescriptorFactory('loment', {
      createDebugAdapterDescriptor() {
        const ed = vscode.window.activeTextEditor;
        const file = ed ? ed.document.uri.fsPath : undefined;
        const inv = debugInvocation({
          root: workspaceRoot(file ? path.dirname(file) : undefined),
          fileDir: file ? path.dirname(file) : undefined,
          platform: process.platform,
          dapScript: cfg().get('dapScript'),
          dapCommand: cfg().get('dapCommand'),
          dapArgs: cfg().get('dapArgs'),
        });
        if (inv.error) {
          // 不能返回 undefined（那会是一个"按了没反应"的调试会话）—— 弹出来说清楚。
          vscode.window.showErrorMessage(`Loment: ${inv.error}`);
          return undefined;
        }
        out().appendLine(`Debug adapter: ${inv.cmd} ${inv.args.join(' ')} (${inv.how})`);
        return new vscode.DebugAdapterExecutable(inv.cmd, inv.args);
      },
    }));

  if (cfg().get('enableLsp') !== false) {
    startClient(context).then((c) => {
      client = c;
    }, (e) => {
      vscode.window.showErrorMessage(`Loment: the language server failed to start: ${e}`);
    });
  }
}

async function deactivate() {
  if (client) {
    await client.stop();
    client = undefined;
  }
}

module.exports = { activate, deactivate };
