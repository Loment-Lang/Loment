# Traits

A trait names a set of methods. An `impl` block provides those methods for one struct. A method
call resolves from the receiver's type at compile time, so the call is direct and the product
carries no dispatch table.

## Declaring and implementing

```rust
trait Measurable {
    fn measure(self) -> u32;
}

struct Small {
    v: u32,
}

impl Measurable for Small {
    fn measure(self) -> u32 {
        return self.v;
    }
}
```

- The trait lists method signatures. Each takes `self` as its first parameter.
- The `impl` block repeats the signature with a body.
- A method is called with `.`, as in `s.measure()`.

## A complete example

```rust,run
module traits

choose runtime

trait Measurable {
    fn measure(self) -> u32;
}

struct Small {
    v: u32,
}

struct Big {
    v: u32,
}

impl Measurable for Small {
    fn measure(self) -> u32 {
        return self.v;
    }
}

impl Measurable for Big {
    fn measure(self) -> u32 {
        return self.v * 10;
    }
}

fn main() {
    let s: Small = Small { v: 7 };
    let b: Big = Big { v: 7 };
    print_num(s.measure());
    print(" ");
    print_num(b.measure());
    print("\n");
}
```
<!-- expect: 7 70 -->

Each call picks the implementation for the receiver's type. The artifact contains two concrete
functions and two direct calls.

## `self` is a value

`self` is the receiver by value, and it follows the same move rule as any struct parameter. If a
method needs to change the receiver, it returns a new value:

```rust
impl Measurable for Small {
    fn measure(self) -> u32 {
        return self.v;
    }
}
```

A method that needs to read and write shared state takes a pointer or a mutable slice instead of
`self`; the language has no `&mut self` method receiver today.

## Methods on more than one type

A trait is useful when several types share a behaviour:

```rust
trait Named {
    fn name(self) -> u32;
}
```

Implement it once per type. The trait itself has no storage and no runtime value; it is a
compile-time agreement about method names and signatures.

## Calling a method from another method

A method body is an ordinary function body. It can call other methods on `self`:

```rust
trait Twice {
    fn base(self) -> u32;
    fn twice(self) -> u32;
}

impl Twice for Small {
    fn base(self) -> u32 {
        return self.v;
    }
    fn twice(self) -> u32 {
        return self.base() * 2;
    }
}
```

The inner call is resolved the same way as any other method call.

## What traits do not provide

- **No trait objects.** There is no `dyn Trait`, and no value that holds "some type implementing
  a trait". The set of implementations is fixed at compile time.
- **No dynamic dispatch.** A method call is a direct call. When the set of alternatives is open,
  an enum with a `match` is the usual representation.
- **No default method bodies in the current subset.** Each `impl` provides every method.
- **No associated types or associated constants.**
- **No generic methods in a trait.**

These are current boundaries. Within them, a trait is a good way to give several structs the same
vocabulary and to keep the call sites uniform.

## Traits and enums

A closed set of alternatives is often better as an enum:

```rust
enum Shape {
    Square(u32),
    Rectangle(u32),
}
```

A `match` on the enum handles every case, and the compiler checks that none is missing. A trait
with two implementations gives the same two behaviours, and the choice of implementation is made
by the value's type. Use a trait when the types are independently defined; use an enum when the
set belongs to one module and the caller must handle every case.

The next chapter moves up one level, from a type to a unit and a package.
