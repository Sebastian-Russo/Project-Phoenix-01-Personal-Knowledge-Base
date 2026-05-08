"""
Fetches and ingests documents from Google Docs and Google Drive.

Google Docs are not files — they're living documents stored in
Google's servers. To read them we need to:
1. Authenticate with Google via OAuth 2.0 (same as Project 10)
2. Use the Google Docs API to export content as plain text
3. Use the Google Drive API to list files in a folder

OAuth 2.0 recap — think of it like a hotel key card:
- You show your ID at the front desk (login with Google)
- Google gives you a key card (access token)
- The key card works for a limited time (token expiry)
- When it expires, your refresh token gets you a new one
  without going back to the front desk (token refresh)

We store the token in google_token.json so you only have to
log in once — subsequent runs use the stored token and refresh
it automatically when it expires.
"""

import json
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    GOOGLE_TOKEN_PATH,
    GOOGLE_SCOPES,
    GOOGLE_SYNC_FOLDER_ID
)


class GDocsIngester:
    """
    Authenticates with Google and fetches document content.

    Lazy authentication — we don't connect to Google until
    the first actual request. This means the app starts fine
    even if Google credentials aren't configured yet.
    """

    def __init__(self):
        self._docs_service  = None
        self._drive_service = None
        self._creds         = None

    # ── Authentication ─────────────────────────────────────────────────────

    def is_authenticated(self) -> bool:
        """Check if valid credentials exist without triggering auth flow."""
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            return False
        try:
            creds = self._load_credentials()
            return creds is not None and creds.valid
        except Exception:
            return False

    def authenticate(self) -> str:
        """
        Trigger the OAuth flow and return an auth URL for the user.
        Called from the Flask /auth/google endpoint.

        The user visits the URL, logs in with Google, and Google
        redirects back to our /auth/callback endpoint with a code
        we exchange for tokens.
        """
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            raise ValueError(
                "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env "
                "before authenticating with Google."
            )

        flow = self._build_flow()
        auth_url, _ = flow.authorization_url(
            access_type   = "offline",    # request refresh token
            prompt        = "consent",    # always show consent screen
            include_granted_scopes = "true"
        )
        return auth_url

    def handle_callback(self, code: str) -> None:
        """
        Exchange the authorization code for tokens and save them.
        Called from the Flask /auth/callback endpoint.
        """
        flow = self._build_flow()
        flow.fetch_token(code=code)
        self._save_credentials(flow.credentials)
        print("[GDocsIngester] Authentication successful — token saved")

    # ── Ingestion ──────────────────────────────────────────────────────────

    def ingest_document(self, doc_id: str) -> dict:
        """
        Fetch a single Google Doc by its document ID.

        The doc_id is the long string in the Google Docs URL:
        docs.google.com/document/d/{DOC_ID}/edit

        Returns a dict ready for DocumentStore.ingest().
        """
        docs = self._get_docs_service()

        try:
            # Fetch document metadata
            doc_meta = docs.documents().get(documentId=doc_id).execute()
            title    = doc_meta.get("title", "Untitled Google Doc")

            # Export as plain text — cleanest format for our purposes
            # The Docs API returns structured JSON but plain text is easier
            # to chunk consistently
            drive    = self._get_drive_service()
            response = drive.files().export(
                fileId   = doc_id,
                mimeType = "text/plain"
            ).execute()

            # export() returns bytes
            text = response.decode("utf-8") if isinstance(response, bytes) else response

            if not text.strip():
                raise ValueError(f"Google Doc is empty: {title}")

            # Get file metadata from Drive for extra context
            file_meta = drive.files().get(
                fileId = doc_id,
                fields = "id,name,modifiedTime,owners,webViewLink"
            ).execute()

            print(f"[GDocsIngester] Fetched: {title} ({len(text)} chars)")

            return {
                "text":        text,
                "title":       title,
                "source_path": f"gdoc://{doc_id}",
                "source_type": "gdoc",
                "extra": {
                    "doc_id":       doc_id,
                    "web_link":     file_meta.get("webViewLink", ""),
                    "modified":     file_meta.get("modifiedTime", ""),
                    "owner":        file_meta.get("owners", [{}])[0].get("displayName", ""),
                }
            }

        except HttpError as e:
            if e.resp.status == 404:
                raise ValueError(f"Google Doc not found: {doc_id}")
            if e.resp.status == 403:
                raise PermissionError(f"No access to Google Doc: {doc_id}")
            raise

    def list_folder(self, folder_id: str = None) -> list[dict]:
        """
        List all Google Docs in a Drive folder.

        Returns a list of file dicts with id, name, and modifiedTime.
        Used by the sync modules to discover documents to ingest.

        If no folder_id provided, uses GOOGLE_SYNC_FOLDER_ID from config.
        If that's also empty, lists all Google Docs the user owns.
        """
        drive     = self._get_drive_service()
        folder_id = folder_id or GOOGLE_SYNC_FOLDER_ID

        # Build query — only fetch Google Docs, not Sheets/Slides/etc.
        if folder_id:
            query = (
                f"'{folder_id}' in parents "
                f"and mimeType='application/vnd.google-apps.document' "
                f"and trashed=false"
            )
        else:
            query = (
                "mimeType='application/vnd.google-apps.document' "
                "and trashed=false"
            )

        try:
            results = drive.files().list(
                q        = query,
                fields   = "files(id,name,modifiedTime,webViewLink)",
                orderBy  = "modifiedTime desc",
                pageSize = 100
            ).execute()

            files = results.get("files", [])
            print(f"[GDocsIngester] Found {len(files)} Google Docs")
            return files

        except HttpError as e:
            if e.resp.status == 403:
                raise PermissionError(f"No access to folder: {folder_id}")
            raise

    def get_modified_since(self, since_iso: str, folder_id: str = None) -> list[dict]:
        """
        List Google Docs modified after a given timestamp.

        Used by realtime sync to find only changed documents
        since the last sync run — much more efficient than
        re-fetching everything every poll cycle.

        since_iso: ISO 8601 timestamp e.g. "2024-01-15T10:30:00Z"
        """
        drive     = self._get_drive_service()
        folder_id = folder_id or GOOGLE_SYNC_FOLDER_ID

        folder_clause = f"'{folder_id}' in parents and " if folder_id else ""

        query = (
            f"{folder_clause}"
            f"mimeType='application/vnd.google-apps.document' "
            f"and modifiedTime > '{since_iso}' "
            f"and trashed=false"
        )

        results = drive.files().list(
            q       = query,
            fields  = "files(id,name,modifiedTime)",
            orderBy = "modifiedTime desc"
        ).execute()

        files = results.get("files", [])
        print(f"[GDocsIngester] {len(files)} docs modified since {since_iso}")
        return files

    # ── Service builders ───────────────────────────────────────────────────

    def _get_docs_service(self):
        """Get authenticated Google Docs API service, building if needed."""
        if not self._docs_service:
            creds = self._ensure_credentials()
            self._docs_service = build("docs", "v1", credentials=creds)
        return self._docs_service

    def _get_drive_service(self):
        """Get authenticated Google Drive API service, building if needed."""
        if not self._drive_service:
            creds = self._ensure_credentials()
            self._drive_service = build("drive", "v3", credentials=creds)
        return self._drive_service

    def _ensure_credentials(self) -> Credentials:
        """
        Load credentials, refreshing if expired.
        Raises if not authenticated yet.
        """
        creds = self._load_credentials()

        if not creds:
            raise PermissionError(
                "Not authenticated with Google. "
                "Visit /auth/google to connect your account."
            )

        # Refresh if expired — like renewing your hotel key card
        # without going back to the front desk
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self._save_credentials(creds)
            print("[GDocsIngester] Token refreshed")

        self._creds = creds
        return creds

    def _load_credentials(self) -> Credentials | None:
        """Load stored credentials from disk."""
        if not GOOGLE_TOKEN_PATH.exists():
            return None
        try:
            return Credentials.from_authorized_user_file(
                str(GOOGLE_TOKEN_PATH),
                GOOGLE_SCOPES
            )
        except Exception:
            return None

    def _save_credentials(self, creds: Credentials) -> None:
        """Persist credentials to disk for reuse across sessions."""
        with open(GOOGLE_TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    def _build_flow(self) -> InstalledAppFlow:
        """Build OAuth flow from client credentials."""
        client_config = {
            "web": {
                "client_id":                GOOGLE_CLIENT_ID,
                "client_secret":            GOOGLE_CLIENT_SECRET,
                "redirect_uris":            [GOOGLE_REDIRECT_URI],
                "auth_uri":                 "https://accounts.google.com/o/oauth2/auth",
                "token_uri":                "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs"
            }
        }
        return InstalledAppFlow.from_client_config(client_config, GOOGLE_SCOPES)

# The get_modified_since method is what makes realtime sync efficient.
# Instead of re-fetching every doc on every poll cycle, it asks Google
# "what changed since I last checked?" and only fetches those documents.
# Without this, a folder with 500 docs would make 500 API calls every 5 minutes
# — with it, most poll cycles make zero calls because nothing changed.
