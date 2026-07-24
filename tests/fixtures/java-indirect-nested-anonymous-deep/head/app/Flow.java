interface Port { void run(); }
interface Inner { void go(); }
class Ledger { void settle() { int value = 2; } }
class Flow {
    Port port;
    void sink() { port.run(); }
    void wire() {
        port = () -> {
            Inner inner = new Inner() {
                public void go() {
                    new Inner() { public void go() { new Ledger().settle(); } }.go();
                }
            };
            inner.go();
        };
    }
}
