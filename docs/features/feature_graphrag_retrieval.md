# Dokumentasi Fitur: GraphRAG Retrieval

## Apa yang Dilakukan Fitur Ini?

Fitur ini tugasnya **nyari informasi di database graf (Neo4j)** buat ngasih jawaban yang lebih dalem ke LLM.

Bedanya sama search biasa: kalau search SQL cari angka-angka, fitur ini cari **hubungan antar data**. Contohnya:
- *"Apa aja sih penyebab engine overheat?"* → bakal nyari node "ENGINE OVERHEAT" dan liat semua yang terhubung
- *"Apa hubungan hydraulic leak sama final drive?"* → bakal liat jalur relasi antar dua entitas

Ada **4 mode** pencarian yang bisa dipake. Masing-masing punya kegunaan beda.

## Alur Kerja (Flowchart)

`mermaid
graph TD
    A[Pertanyaan Kamu] --> B{Pilih Mode}
    
    B -->|Local| C[Local Search:
Cari entitas cocok
di graf, ambil tetangga
1-hop langsung]
    
    B -->|Global| D[Global Search:
Cari node Community
yang relevan,
baca summary-nya]
    
    B -->|DRIFT| E[DRIFT Search:
Cari entitas,
iterasi lompat relasi
multi-hop pake LLM]
    
    B -->|Hybrid| F[Hybrid Search:
Local + Global
digabung jadi
satu konteks]
    
    C --> G[Kumpulin semua
fakta semantik]
    D --> G
    E --> G
    F --> G
    
    G --> H[Jadi teks konteks
buat LLM ngejawab]
```

## Perbandingan Mode

| Mode | Cocok buat | Cara Kerja | Kecepatan |
|------|-----------|------------|-----------|
| **Local** | Pertanyaan detail spesifik | Cari entitas → ambil tetangga 1-hop (yang nyambung langsung) | ⚡ Cepat |
| **Global** | Tren/pola umum di banyak data | Cari Community → baca rangkumannya | ⚡ Cepat |
| **Hybrid** | Kombinasi detail + konteks luas | Local + Global digabung | 🐢 Sedang |
| **DRIFT** | Fakta tersembunyi, jalur relasi kompleks | Cari entitas → LLM mutusin lompat ke mana selanjutnya | 🐌 Lambat (pake LLM berulang) |

## Input → Proses → Output

### Input
- `query`: pertanyaan kamu (string)
- `mode`: "local", "global", "hybrid", atau "drift"

### Proses

**Local Search (step-by-step):**
1. Cari entitas yang cocok di Neo4j pake vector search
2. Ambil semua node tetangga yang terhubung langsung (1-hop)
3. Kumpulin properti dari semua node itu jadi teks

**Global Search (step-by-step):**
1. Cari node `Community` yang relevan
2. Baca properti `summary` dari komunitas itu
3. Gabungin semua summary jadi teks

**Hybrid Search (step-by-step):**
1. Jalanin Local Search
2. Jalanin Global Search
3. Gabungin hasil keduanya

**DRIFT Search (step-by-step):**
1. Mulai kayak Local Search (cari entitas awal)
2. Tapi LLM bakal milih: "lompat ke node mana lagi yang relevan?"
3. Lompat terus samah dirasa cukup
4. Paling lambat tapi bisa dapet fakta yang gak kelihatan di permukaan

### Output
String teks yang berisi fakta-fakta dari graf. Teks ini bakal dipake LLM buat ngejawab pertanyaan kamu.

## Kode Contoh (Simplified)

```python
# File: src/graph/retrieval/local.py / hybrid.py / drift.py

class GraphRAGRetriever:
    def retrieve_context(self, query: str, mode: str = "local") -> str:
        if mode == "local":
            return self.local_retriever.search(query)
        elif mode == "global":
            return self.global_retriever.search(query)
        elif mode == "hybrid":
            local = self.local_retriever.search(query)
            global_ = self.global_retriever.search(query)
            return local + "\n---\n" + global_  # digabung
        elif mode == "drift":
            return self.drift_retriever.search(query)
```

## Catatan Penting untuk Pengembang Selanjutnya

1. **Local Search** = nanya detail spesifik. Misal: "Apa aja komponen yang terlibat di hydraulic leak?" → jawabannya detail, spesifik, cuma yang nyambung langsung.

2. **Global Search** = nanya gambaran besar. Misal: "Apa tren kerusakan engine secara umum?" → jawabannya dari summary komunitas, lebih luas.

3. **Hybrid** itu pilihan paling aman. Cocok buat pertanyaan yang gak jelas butuh detail atau gambaran besar. Sistem jalanin 2 mode sekaligus, hasilnya digabung.

4. **DRIFT itu paling mahal** (pake LLM berulang kali). Makanya dipake kalo emang perlu aja, misal buat investigasi mendalam.

5. **Yang dipanggil dari `ask_emr_graph` tool.** Kamu gak perlu pusing mikirin mode apa yang dipake — tool `ask_emr_graph` di `src/agent/tools.py` yang ngatur ini semua.
`