from django.urls import path

from api import views


urlpatterns = [
    path("", views.home, name="home"),
    path("google-login", views.google_login, name="google-login"),
    path("register", views.register, name="register"),
    path("verify", views.verify, name="verify"),
    path("login", views.login, name="login"),
    path("history", views.history, name="history"),
    path("save-history", views.save_history, name="save-history"),
    path("data-consent", views.data_consent, name="data-consent"),
    path("chat", views.career_chat, name="chat"),
    path("recommend", views.recommend, name="recommend"),
    path("resend-code", views.resend_code, name="resend-code"),
    path("forgot-password", views.forgot_password, name="forgot-password"),
    path("change-username", views.change_username, name="change-username"),
    path("change-password", views.change_password, name="change-password"),
    path("delete-account", views.delete_account, name="delete-account"),
    path("scrape", views.scrape_data, name="scrape"),
    path("send-report", views.send_report, name="send-report"),
]
