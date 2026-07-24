interface Port { void run(); }
interface Inner { void go(); }
class Ledger { void settle() { int value = 2; } }
class Flow {
    Port port;
    void sink() { port.run(); }
    void wire() {
        port = () -> {
            Inner inner = () -> {
                Inner deeper = () -> new Ledger().settle();
                deeper.go();
            };
            inner.go();
        };
    }
}
