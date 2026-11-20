from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameBackend(ModelBackend):
    """Let people sign in with either their username or their email address."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if not username or password is None:
            return None
        user = User.objects.filter(username__iexact=username).first() or \
            User.objects.filter(email__iexact=username).first()
        if user is None:
            # Run the password hasher anyway (as Django's own ModelBackend does) so an unknown account takes as long to
            # reject as a wrong password: response time must not reveal which e-mail addresses are registered.
            User().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
