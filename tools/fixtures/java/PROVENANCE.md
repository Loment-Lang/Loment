# Java AOT fixtures

These `.class` files are **real javac output**, not hand-written class files.

Generated on 2026-10-21 with:

```
javac 23 (Oracle JDK 23)
javac --release 8 -d classes src/com/example/MathLib.java src/com/example/BranchLib.java \
                            src/com/example/LongLib.java
```

Sources are next to the class files. They are compiled with `--release 8` so the
fixtures avoid `invokedynamic` string concatenation; the first slice does not
lower `invokedynamic` yet.

SHA-256 of the committed class files:

```
bb4a79bd7b16f03bdee428d7fefa26729c7094ebdf54093c5d707d997acac8cb  MathLib.class
27c065d90578c87f3e1496a2f586d5a905888173693d2f3c688b68d241e297a7  BranchLib.class
4b2b6f736e762afad4cd26ed9516e2363d945712464ce082681b3f08ba3f96fb  LongLib.class
```

`loment_java_test.py` uses these files and does **not** invoke `java`, `javac`
or `jar`; it only parses the bytes and emits Loment IR.
