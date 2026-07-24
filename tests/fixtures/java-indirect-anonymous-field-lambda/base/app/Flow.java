interface Hook { void go(); }
interface Task { void exec(); }
class Vault { void transfer() { int value = 1; } }
class Flow {
    void sink() {
        Hook hook = new Hook() {
            Task inner = () -> new Vault().transfer();
            public void go() { inner.exec(); }
        };
        hook.go();
    }
}
