CREATE TABLE habits (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT,
    frequency TEXT,
    goal TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE completions (
    id INTEGER PRIMARY KEY,
    habit_id INTEGER,
    date TEXT,
    completed BOOLEAN,
    FOREIGN KEY (habit_id) REFERENCES habits(id)
);
