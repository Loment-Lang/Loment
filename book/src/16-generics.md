# Generics

A generic function or struct is written once with a type parameter and compiled once per concrete
type it is used with. There is no runtime type information and no dispatch cost: each call is a
direct call to a specialised copy.

## Generic functions

```rust
fn max_of<T>(a: T, b: T) -> T {
    if a > b {
        return a;
    }
    return b;
}
```

The call site supplies the type through its arguments:

```rust
let m: u32 = max_of(7 as u32, 9 as u32);
```

Both arguments must have the same type; `T` is one type, not a family. Untyped literals are not
enough: write `7 as u32`, or bind the values to typed names first. The compiler monomorphises
`max_of` for `u32`, `i64`, or whatever type the program actually uses.

Generic declarations use the `fn` spelling. `def` is sugar for non-generic functions today.

## Generic structs

A type parameter can appear in a struct:

```rust
struct Pair<T> {
    a: T,
    b: T,
}
```

A literal supplies concrete fields, and the field types determine `T`:

```rust,run
module generic_pair

choose runtime

struct Pair<T> {
    a: T,
    b: T,
}

fn max_of<T>(a: T, b: T) -> T {
    if a > b {
        return a;
    }
    return b;
}

fn main() {
    let p: Pair<u32> = Pair { a: 3, b: 9 };
    print_num(max_of(p.a, p.b));
    print("\n");
    let q: Pair<i32> = Pair { a: -4, b: -9 };
    print_num(max_of(q.a, q.b));
    print("\n");
}
```
<!-- expect: 9
-4 -->

The struct is lowered once per instantiation. `Pair<u32>` and `Pair<i32>` are separate types with
separate field layouts, even though the source has one declaration.

## Generic calls compose

A generic function can call another generic function:

```rust
fn identity<T>(a: T) -> T {
    return a;
}

fn twice<T>(a: T) -> T {
    return identity(a);
}
```

The specialisation of `twice<u32>` calls `identity<u32>`. The call graph stays static, and the
compiler emits both copies.

## What a type parameter can be

The type parameter is replaced by a concrete type at compile time, and the body must make sense
for every instantiation. A comparison `a > b` requires an ordered type; instantiating the
function with `bool` would fail at check time. The checker validates each instantiation, not the
generic body in isolation.

Usable instantiations today are the scalar types: integers, `bool`, `str` and `ptr` where the
operations allow them. Structs and enums as type arguments are not part of the working set.

## The prelude enums are generic

`Option<T>` and `Result<T, E>` are written with type arguments:

```rust
def parse(v: u32) -> Result<u32, u32> {
    if v == 0 {
        return Result::Err(1);
    }
    return Result::Ok(v);
}
```

Construction uses the plain enum name; the type arguments belong to the declaration in the
signature. A `match` over a generic enum uses the plain name too, and the value being matched
should be bound to a typed local first:

```rust
let r: Result<u32, u32> = parse(3);
match r {
    Result::Ok(v) => { return v; }
    Result::Err(e) => { return 0; }
}
```

Matching directly on a call expression can confuse the monomorphiser; the typed local is the
reliable form.

## What generics do not provide

- **No trait bounds in the declaration.** The body is checked per instantiation instead.
- **No generic methods in traits.** A trait method is concrete for one type.
- **No runtime polymorphism.** There is no type erasure and no vtable.
- **No generic `def`.** Generic declarations use `fn`.
- **No variadic type parameters.** Each parameter is one type.

These limits keep the compiler's monomorphiser small. When a problem needs a family of types with
shared behaviour, the usual alternative is an enum with one variant per type, plus a `match`.

## When to use a generic

Use one when the algorithm is identical for several scalar types and the type appears in the
signature. `max_of`, `identity`, `swap`, and container shapes are good fits. When the type
appears in a payload that needs different behaviour, an enum is clearer: it makes the set of
cases explicit, and `match` checks that every case is handled.

The next chapter adds the other abstraction tool: traits.
