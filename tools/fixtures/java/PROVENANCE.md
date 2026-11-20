# Java AOT fixtures

These `.class` files are **real javac output**, not hand-written class files.

Generated on 2026-10-21 with:

```
javac 23 (Oracle JDK 23)
javac --release 8 -d classes src/com/example/MathLib.java src/com/example/BranchLib.java \
                            src/com/example/LongLib.java src/com/example/MultiLib.java \
                            src/com/example/ArrayLib.java src/com/example/ObjLib.java \
                            src/com/example/StringLib.java src/com/example/InstanceLib.java \
                            src/com/example/StaticLib.java src/com/example/ExcLib.java \n                            src/com/example/PrintLib.java \n                            src/com/example/StrEq.java
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
a8a472bbf1a2a37dabbb87084c7b8400cb83954799d3dd85861d61bc79a7654a  PrintLib.class
274dc509c73bd439d0853941f3fec6f1108c210cc8dfa2095657925d02084f26  StrEq.class
9e0550e7604deb5e5841f4cf76873272d35467dbf31d06a8a05f8c7cb73626fe  ClassA.class
8ac841eb0765f768c8fa2b7b75e355d2cea5d664aa05b937fbc1a61cf5dbb340  ClassB.class
bdf27f66639ae96aae6a24b0da6ad6b976857b128f98c9a0bff0727a81f3ee01  multiclass.jar
```

`PrintLib` is the jrt printing fixture (`System.out`/`err`, `Math`); `StrEq`
covers `String.equals` / `hashCode` (ASCII and `中文`). Their expected values
are in `tools/loment_java_test.py`.

`multiclass.jar` contains `com/example/ClassA.class` and `com/example/ClassB.class`;
it is the multi-class fixture (cross-class `new`, static field, `invokestatic`).

`loment_java_test.py` uses these files and does **not** invoke `java`, `javac`
or `jar`; it only parses the bytes and emits Loment IR.

## 2026-11-02 additions (inheritance and switches)

`shapelib.jar` and `switchlib.jar` were compiled the same day with the same
`javac 23` and `--release 8` from `src/com/example/{ShapeLib,SwitchLib}.java`,
then packed with Python's `zipfile` (the machine has no `jar` on `PATH`):

```
javac --release 8 -d classes src/com/example/ShapeLib.java src/com/example/SwitchLib.java
```

`ShapeLib` is a three-level hierarchy (`ShapeLib$Shape`, `$Square`, `$Rect`)
with an override in each subclass; `SwitchLib` has one dense switch
(`tableswitch`) and one sparse switch (`lookupswitch`).

```
64e50e5d1a7534dc219b98cc256be15c4f46085f7677e7f6b221691340c3ac9c  shapelib.jar
2de47b44d6dcb8f81f24add791a9ad7afcdd36a80b1745a246fc8584c88d6d10  switchlib.jar
```

`castlib.jar` (object arrays, `instanceof`, `checkcast`), same toolchain:

```
b95df01d513e8cfe56249a208f72c865913f9dbb988d1e53c16bec32b2a98cdb  castlib.jar
```

Runtime pieces (#1060), same `javac 23`:

```
# --release 8: StringBuilder calls, no invokedynamic
65180b143db28bd20414ede60b498ce6ffac11f8719ff38fa831fe03f509693c  sblib.jar
4f876b5a8f5264b01779f8b21cee7bc2408b01dfbc1dbb86be1b2207b285a2ce  throwlib.jar  (message + caughtMessage)
# --release 17: `+` becomes invokedynamic (StringConcatFactory), boxes via valueOf
4e9dea923423550d38053e00d16ca95993ce4dd1d3c83a9f30eca35bc79b027d  concatlib.jar
20751afefaaead43b34481670eb15b0a84efd95524c48e35d0b46f4415fec428  boxlib.jar
```

`initlib.jar` (class-init order and laziness), `--release 8`:

```
5976e0d72dd45165958336472e7fb041a9476aad69ed1b0dd6533394ef1a55fc  initlib.jar
```

`ifacelib.jar` (interface dispatch), `--release 8`:

```
84a526619f10bc9bb01bd8eb55696f163ce78e4e3da140ff488d6ae574a7500e  ifacelib.jar
```
