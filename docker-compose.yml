services:
  maltai:
    build: .
    # En local : http://localhost:7000
    # Sur Coolify : le domaine + HTTPS sont gérés par le proxy, retire ports.
    ports:
      - "7000:7000"
    environment:
      APP_BIND: "0.0.0.0"
      APP_PORT: "7000"
      AUTH_ENABLED: "true"
      # Mets true derrière un proxy HTTPS (Coolify), false en HTTP local.
      SECURE_COOKIES: "false"
      # Recommandé : fixe-les pour ne pas dépendre des logs / d'un secret généré.
      # SESSION_SECRET: "change-moi-32-caracteres-aleatoires"
      # MALTAI_ADMIN_USER: "admin"
      # MALTAI_ADMIN_PASSWORD: "ton-mot-de-passe-fort"
    volumes:
      - maltai_data:/app/data
    restart: unless-stopped

volumes:
  maltai_data:
