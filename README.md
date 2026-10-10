# Vinyl Vault

A cloud-native web application for cataloging music albums, built with **FastAPI**, **PostgreSQL**, and **Supabase Storage**.

---

## Architecture Diagram

```text
       +-----------------------------------------------------------+
       |                  Web Application (Browser)                |
       |  - index.html single-page UI                              |
       |  - Dynamic glowing cards & interactive CRUD gallery       |
       +-----------------------------------------------------------+
                        |                          ^
       POST / PUT / GET |                          | Loads Cover Image
       DELETE API calls |                          | via Public URL
                        v                          |
       +-----------------------------------------------------------+
       |                    PaaS Host (Render)                     |
       |  FastAPI Backend (main.py)                                |
       |  - Validates 4 types: String, Integer, Boolean, Array     |
       |  - Dispatches async worker via BackgroundTasks            |
       +-----------------------------------------------------------+
              | (Cover Art file)       | (SQL CRUD)           |
              v                        v                      |
   +---------------------+   +---------------------+          |
   | Supabase Storage    |   | Supabase PostgreSQL |          |
   | (album-covers)      |   | (albums table)      |          |
   +---------------------+   +---------------------+          |
              ^                                  ^            |
              | Downloads image                  | Updates    |
              +-------------+      +-------------+ hex color  |
                            |      |                          |
                     +-----------------------------------+    |
                     | Background Worker (worker.py)     |<---+
                     | - Extracts dominant color via PIL |
                     +-----------------------------------+