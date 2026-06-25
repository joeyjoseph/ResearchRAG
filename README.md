# Research RAG

A research infrastructure toolkit originally built to support a nonfiction book project, but applicable to so much more. It turns a large folder of source documents and an author's own writing into a searchable, indexed, relational corpus that, when paired with a local chat inteface, can answer questions, surface connections across sources, and generate research memos with (after nearly two months of testing) no halucinations.

When using common LLM chat programs like Claude or ChatGPT, when the user asks a question outside the realm of the model's training data, the robot has to spend time searching the web, reading documents, and "thinking". The token costs can add up. Documents must be searched multiple times to answer questions. The most common way to improve the speed and reliability of those systems is to upload files that they cache into their memory. But even that has its limits. When the user asks a question about particular documents, the robot has to read those documents anew if the session is new or sufficiently stale.

This tool takes your curated collection of materials, and creates a RAG database. RAG stands for Retrieval-Augmented Generation, and it allows a pre-existing LLM to use domain-specific data that it was not trained on. The system places your coprus into a hybrid SQLite metadata index, and ChromaDB vector index for semantic search. You can think of a vector database as almost a proto-LLM, as its text embeddings are similar to what LLMs are under the hood. The robot doesn't need to read every document anew to know: where relevant data is, and the connections between concepts across the entire corpus. The vector database allows the system to sorta grok it.

What this means is that you get better results, faster, and at lower cost than using an LLM alone. It also means you can completely automate research tasks should you desire.

It's nothing fancy; just a set of Python scripts that create the database and run nightly to keep everything up to date. There's an HTML dashboard for tracking corpus growth, manuscript progress, and AI usage/cost. But you don't have to use it.

## The Core Components

Other than the aforementioned SQL and Vector database duet, the system is quite simple and consists of four parts.

1. A suite of scripts to manage the database
2. A program that allows the user to run local LLMs for some or all tasks (yes this can be done 100% local for free if you have enough time or RAM)
3. An API key to a larger reliable frontier model for some reasoning tasks and speedy interactions
4. An ai agent called Hermes that acts as chat interface for project questions, as well as a powerful manager of the system 

## Pre-configuration
Before proceeding to the [INSTALL AND SETUP guide](<INSTALL AND SETUP.md>) take a moment to think about your corpus. You don't have to over-do it, but there are things you can do to make sure you have a clean, intelligible structure that is easy to add, remove, or edit.

The **Golden Rule of Data** applies: **Garbage in: Garbage out**. Some general guidelines:

- We picked plaintext files, but .md or whatever else are proabably fine. Try to pick one and stick with it. The less you have to manage the better. Not all of your first group of files have to be this format. But the script that will generate and automatically add to the database  converts everything to txt, so you'll need to change it if you prefer something else, or want more than one type.
- Creat a directory structure. You'll see how we did it in the Install guide, but each project is different. You'll need to decided on this before you begin. If you don't have, and don't anticipate dealing with interviews, then don't make an `Interviews/` folder
- If your files are ready, place them in their appropriate folders. If you have some that need converting, place them in the `Add To Corpus/` folder.
- Once you're ready, you can move on to the [INSTALL AND SETUP guide](<INSTALL AND SETUP.md>).

For more detailed information about how the system works, check out [System Documentation](<System Documentation.md>).
