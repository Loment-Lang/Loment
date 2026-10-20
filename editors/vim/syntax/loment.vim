" editors/vim/syntax/loment.vim — Loment (.lomt) 语法高亮
"
" 与 editors/vscode/syntaxes/loment.tmLanguage.json 是同一套语言的两种表达;
" 关键字/类型词表必须与它一致 —— tools/loment_editors_test.py 会交叉核对, 漂了就红。
"
" 用法 (不动系统配置也能试):
"   vim -N -c 'set rtp+=<仓库>/editors/vim' -c 'filetype plugin on' foo.lomt
"
" 两个 Vim 特有的坑 (实测踩过, 都写在对应位置):
"   1. 同一起点"后定义者胜" —— 注释规则必须放**最后**, 否则 `//` 被单字符运算符 `/` 抢走;
"   2. `\zs` 不能用来"跳过关键字突出后面的名字" —— 引擎在关键字匹配后已跳过那段文本,
"      要用 `nextgroup` + `contained`。

if exists('b:current_syntax')
  finish
endif

" ---- 关键字 (与 TextMate 语法同一张表)
syn keyword lomentKeyword let if else while for in match
syn keyword lomentKeyword const return mut pub use module as
" Loment 独有
syn keyword lomentKeyword guard excluded interrupt
syn keyword lomentBool true false
syn keyword lomentSelf self

" ---- 类型 / 内建
syn keyword lomentType u8 u16 u32 u64 i8 i16 i32 i64 bool str ptr
" 内建表 == tools/lomentc.py 的 BUILTINS。load16/load32/store16/store32 **不在这里** ——
" 它们是 loment/examples/bytes.lomt 里的普通函数, 不是内建 (2026-10-12 摘掉)。
syn keyword lomentBuiltin load8 store8
syn keyword lomentBuiltin alloc free panic
syn keyword lomentBuiltin str_len str_byte str_ptr str_eq str_concat slice_len
syn keyword lomentBuiltin syscall4 syscall6 syscall7 thread_spawn
syn keyword lomentBuiltin get_bits set_bits atomic_add ptr_add ptr_sub inb outb

" ---- 声明名: `fn add` / `struct Pair` / `capability blk` 里的那个名字
" (nextgroup: 匹配完声明关键字后, 下一个词按 contained 项上色)
syn match lomentKeyword "fn" nextgroup=lomentFuncName skipwhite
syn match lomentKeyword "\<struct\>" nextgroup=lomentTypeName skipwhite
syn match lomentKeyword "\<enum\>" nextgroup=lomentTypeName skipwhite
syn match lomentKeyword "\<trait\>" nextgroup=lomentTypeName skipwhite
syn match lomentKeyword "\<capability\>" nextgroup=lomentCapName skipwhite
syn match lomentFuncName "\w\+" contained
syn match lomentTypeName "\w\+" contained
syn match lomentCapName "\w\+" contained

" ---- 开关 / 核心维 / 表层语法声明 (docs/182 / docs/188 / docs/185 / docs/223)
" `set` `choose` `addin` `command` `foruse` `register` `write` `grammar` **都不是保留字**
" (参考实现接受关键字做标识符: `fn set() -> u32` 合法), 所以按**形状**认, 不按词认 ——
" 写成 syn keyword 会把 `let set: u32` 也染色。与 TextMate 语法同一条判据。
" 通用形在前、具体形在后: Vim 同一起点上**后定义者胜**, 具体形要能盖住整句。
syn match lomentKeyword "\<choose\>\s\+\w\+"
syn match lomentKeyword "\<addin\>\s\+\w\+"
syn match lomentKeyword "\<command\>\s\+\w\+"
syn match lomentKeyword "\<foruse\>\s\+\w\+"
syn match lomentKeyword "\<register\>\s\+\w\+"
syn match lomentKeyword "\<choose\>\s\+write\s\+grammar\s\+\S\+"
syn match lomentKeyword "\<set\>\s\+choose\>\s\+\w\+"
syn match lomentKeyword "\<choose\>\s\+close\>\s\+\w\+"
syn match lomentCore    "\<choose\>\s\+\%(std\|no_std\|gc_manual\|gc_auto\|gc_auto_alpha\|runtime\|no_runtime\)\>"
" ---- LSPE 的词法旁路 (docs/262 §5.16 / #786): `lspe pass "reason" { … }`
" `lspe` / `pass` 都不是保留字, 所以按**成对形状**认 (与 TextMate 语法同一条判据)。
syn match lomentKeyword "\<lspe\>\s\+pass\>"

" ---- 数字 (十进制 / 0x) 与字符串 (含转义; 跨行也可以)
syn match lomentNumber "\<\d\+"
syn match lomentNumber "\<0[xX][0-9a-fA-F]\+"
syn match lomentEscape contained "\\\\\|\\\"" 
syn region lomentString start=+"+ skip=+\\\\\|\\"+ end=+"+ contains=lomentEscape

" ---- 运算符
syn match lomentOperator "->\|=>\|::\|==\|!=\|<=\|>=\|&&\|||\|<<\|>>"
syn match lomentOperator "[-+*/%=<>!&|^?]"

" ---- 注释 (必须在最后: 见文件头坑 1); /// 文档注释要排在 // 之后 (后定义者胜)
syn keyword lomentTodo contained TODO FIXME XXX 注意
syn match lomentComment "//.*$" contains=lomentTodo
syn match lomentDocComment "///.*$" contains=lomentTodo
syn region lomentComment start="/\*" end="\*/" contains=lomentTodo keepend

hi def link lomentKeyword    Keyword
hi def link lomentCore       Constant
hi def link lomentBool       Boolean
hi def link lomentSelf       Special
hi def link lomentType       Type
hi def link lomentBuiltin    Function
hi def link lomentFuncName   Function
hi def link lomentTypeName   Type
hi def link lomentCapName    Special
hi def link lomentNumber     Number
hi def link lomentEscape     SpecialChar
hi def link lomentString     String
hi def link lomentOperator   Operator
hi def link lomentComment    Comment
hi def link lomentDocComment Comment
hi def link lomentTodo       Todo

let b:current_syntax = 'loment'
