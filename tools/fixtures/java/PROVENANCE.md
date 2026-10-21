# Java AOT fixtures

These `.class` files are **real javac output**, not hand-written class files.

Generated on 2026-10-21 with:

```
javac 23 (Oracle JDK 23)
javac --release 8 -d classes src/com/example/MathLib.java src/com/example/BranchLib.java \
                            src/com/example/LongLib.java src/com/example/MultiLib.java \
                            src/com/example/ArrayLib.java src/com/example/ObjLib.java \
                            src/com/example/StringLib.java src/com/example/InstanceLib.java \
                            src/com/example/StaticLib.java src/com/example/ExcLib.java
```

Sources are next to the class files. They are compiled with `--release 8` so the
fixtures avoid `invokedynamic` string concatenation; the first slice does not
lower `invokedynamic` yet.

SHA-256 of the committed class files:

```
bb4a79bd7b16f03bdee428d7fefa26729c7094ebdf54093c5d707d997acac8cb  MathLib.class
27c065d90578c87f3e1496a2f586d5a905888173693d2f3c688b68d241e297a7  BranchLib.class
4b2b6f736e762afad4cd26ed9516e2363d945712464ce082681b3f08ba3f96fb  LongLib.class
f6cf5c1ec80d53b622ea0c37d7e316d6f5b92e90cff924bd75ab7854dacaf14a  MultiLib.class
5757c498eb3e5818298a4842adfb102274b21d803d0644ad9cb38293a61b9684  ArrayLib.class
745fab7c823a8b1118c273f56abab95b70635093fddfefec7edd84510e469ddd  ObjLib.class
90bd62325f630788d7edb52b62ea0eec065374bdf6bcb13255f6a634f526b70e  StringLib.class
559d36ae8b55150ce4a3f5366394865d1cd2469a5f30c6c0a25ef8e9d21dffb2  InstanceLib.class
5234cc7a7c3242c08764c06b043c49a8fbd9be85667974ad08e1e9d4081ccfcc  StaticLib.class
0b179a534533c40a1f8c036a13c954dc2192d1484b0823e862126e4d8710afa5  ExcLib.class
```

`loment_java_test.py` uses these files and does **not** invoke `java`, `javac`
or `jar`; it only parses the bytes and emits Loment IR.
