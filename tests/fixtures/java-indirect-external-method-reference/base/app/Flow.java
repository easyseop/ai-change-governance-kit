import java.util.List;

class Vault { void transfer(String row) { int value = 1; } }
class Flow {
    void sink(List<String> rows) {
        Vault vault = new Vault();
        rows.forEach(vault::transfer);
    }
}
