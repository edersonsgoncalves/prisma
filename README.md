# Prisma (FinOrg 2.0)

Prisma is a comprehensive personal finance management system designed to help users track their spending, manage multiple bank accounts, and analyze their financial health through a modern, web-based interface.

## 🚀 Features

- **Dashboard:** At-a-glance view of your financial status, including account balances and monthly spending.
- **Transaction Management:** Easily log income, expenses, and transfers between accounts. Supports recurring transactions and installments.
- **Account & Card Tracking:** Manage various account types, including checking, savings, and credit cards.
- **Credit Card Invoices:** Specialized handling for credit card billing cycles and invoice management.
- **Data Import:** Import transactions via OFX files or PDF credit card invoices.
- **Reporting:** Detailed reports by category and project.

## 🛠 Tech Stack

- **Backend:** FastAPI (Python)
- **Database:** MySQL with SQLAlchemy ORM
- **Frontend:** Jinja2 Templates, HTML5, CSS3, JavaScript
- **Containerization:** Docker & Docker Compose

## 📦 Getting Started

### Prerequisites

- Docker and Docker Compose installed on your machine.

### Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/prisma.git
    cd prisma
    ```

2.  **Set up environment variables:**
    Create a `.env` file in the root directory based on the `.env.example` (if provided) or configure the following:
    ```env
    DB_USER=root
    DB_PASS=yourpassword
    DB_HOST=db
    DB_NAME=prisma
    APP_SECRET_KEY=your_secret_key
    ```

3.  **Start the application:**
    ```bash
    docker-compose up -d --build
    ```

4.  **Access the application:**
    Open your browser and navigate to `http://localhost:7777`.

## 🧪 Running Tests

(Initial test setup in progress)

To run tests:
```bash
pytest
```

## 📝 Analysis & Roadmap

For a detailed analysis of the current architecture and suggested improvements, see [ANALYSIS_REPORT.md](./ANALYSIS_REPORT.md).

## 📄 License

[License Information]
