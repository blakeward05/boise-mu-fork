import { Component, signal, computed, ChangeDetectionStrategy, inject, OnInit, OnDestroy } from '@angular/core';
import { ReactiveFormsModule, FormBuilder, Validators } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { AuthService } from '../auth.service';
import { UserService } from '../user.service';
import { SidenavService } from '../../services/sidenav/sidenav.service';
import { ConfigService } from '../../services/config.service';
import { SystemService } from '../../services/system.service';
import { SessionService } from '../../session/services/session/session.service';

interface AuthProviderPublicInfo {
  provider_id: string;
  display_name: string;
  logo_url?: string;
  button_color?: string;
}

interface AuthProviderPublicListResponse {
  providers: AuthProviderPublicInfo[];
}

@Component({
  selector: 'app-login',
  imports: [ReactiveFormsModule],
  styleUrl: './login.page.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="fixed inset-0 flex items-center justify-center bg-gray-50 dark:bg-gray-900 overflow-y-auto">
      <div class="w-full max-w-md px-4 py-12">
        <!-- Logo -->
        <div class="mb-8 flex justify-center">
          <img
            src="/img/logo-light.png"
            alt="Logo"
            class="size-16 dark:hidden">
          <img
            src="/img/logo-dark.png"
            alt="Logo"
            class="hidden size-16 dark:block">
        </div>

        <div class="bg-white dark:bg-gray-800 rounded-lg shadow-sm p-8">
          <div class="flex flex-col items-center gap-6">
            <div class="flex flex-col items-center gap-2">
              <h1 class="text-2xl font-semibold text-gray-900 dark:text-gray-100">
                Sign In
              </h1>
              <p class="text-base/7 text-gray-600 dark:text-gray-400 text-center">
                Sign in to continue
              </p>
            </div>

            @if (errorMessage()) {
              <div class="w-full p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg" role="alert">
                <div class="flex items-start gap-3">
                  <svg class="size-5 text-red-600 dark:text-red-400 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <p class="text-sm text-red-800 dark:text-red-300">{{ errorMessage() }}</p>
                </div>
              </div>
            }

            <!-- Local login form -->
            @if (showLocalForm()) {
              <form [formGroup]="localForm" (ngSubmit)="handleLocalLogin()" class="w-full flex flex-col gap-4" novalidate>
                <div>
                  <label for="email" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Email</label>
                  <input
                    id="email"
                    type="email"
                    formControlName="email"
                    autocomplete="email"
                    class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    placeholder="you@example.com"
                    [attr.aria-invalid]="localForm.get('email')?.invalid && localForm.get('email')?.touched"
                  />
                  @if (localForm.get('email')?.touched && localForm.get('email')?.errors?.['required']) {
                    <p class="mt-1 text-xs text-red-600 dark:text-red-400" role="alert">Email is required</p>
                  }
                  @if (localForm.get('email')?.touched && localForm.get('email')?.errors?.['email']) {
                    <p class="mt-1 text-xs text-red-600 dark:text-red-400" role="alert">Enter a valid email address</p>
                  }
                </div>

                <div>
                  <label for="password" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Password</label>
                  <input
                    id="password"
                    type="password"
                    formControlName="password"
                    autocomplete="current-password"
                    class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    placeholder="&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;"
                    [attr.aria-invalid]="localForm.get('password')?.invalid && localForm.get('password')?.touched"
                  />
                  @if (localForm.get('password')?.touched && localForm.get('password')?.errors?.['required']) {
                    <p class="mt-1 text-xs text-red-600 dark:text-red-400" role="alert">Password is required</p>
                  }
                </div>

                <button
                  type="submit"
                  [disabled]="isLoading() || localForm.invalid"
                  class="w-full px-4 py-3 text-white font-medium rounded-lg transition-all duration-200 flex items-center justify-center gap-3 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  @if (isLoading() && localLoginActive()) {
                    <div class="size-5 border-2 border-white border-t-transparent rounded-full animate-spin" aria-hidden="true"></div>
                    <span>Signing in...</span>
                  } @else {
                    <span>Sign In</span>
                  }
                </button>
              </form>
            }

            <!-- SSO section -->
            @if (showSsoSection()) {
              <!-- Divider between local form and SSO -->
              @if (showLocalForm()) {
                <div class="relative w-full">
                  <div class="absolute inset-0 flex items-center" aria-hidden="true">
                    <div class="w-full border-t border-gray-200 dark:border-gray-700"></div>
                  </div>
                  <div class="relative flex justify-center text-xs">
                    <span class="bg-white dark:bg-gray-800 px-2 text-gray-500 dark:text-gray-400">or continue with</span>
                  </div>
                </div>
              }

              <div class="w-full flex flex-col gap-3">
                <!-- Primary OIDC SSO button -->
                @if (authService.isOidcConfigured()) {
                  <button
                    type="button"
                    (click)="handleSsoLogin()"
                    [disabled]="isLoading()"
                    class="w-full px-4 py-3 text-white font-medium rounded-lg transition-all duration-200 flex items-center justify-center gap-3 bg-blue-600 hover:bg-blue-700 disabled:opacity-60"
                  >
                    @if (isLoading() && !localLoginActive() && !activeProviderId()) {
                      <div class="size-5 border-2 border-white border-t-transparent rounded-full animate-spin" aria-hidden="true"></div>
                      <span>Connecting...</span>
                    } @else {
                      <svg class="size-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                      </svg>
                      <span>Sign in with SSO</span>
                    }
                  </button>
                }

                <!-- Federated providers from admin-configured list -->
                @for (provider of providers(); track provider.provider_id) {
                  <button
                    type="button"
                    (click)="handleProviderLogin(provider)"
                    [disabled]="isLoading()"
                    class="w-full px-4 py-3 text-white font-medium rounded-lg transition-all duration-200 flex items-center justify-center gap-3 disabled:opacity-60"
                    [style.background-color]="provider.button_color || '#2563eb'"
                  >
                    @if (isLoading() && activeProviderId() === provider.provider_id) {
                      <div class="size-5 border-2 border-white border-t-transparent rounded-full animate-spin" aria-hidden="true"></div>
                      <span>Connecting...</span>
                    } @else {
                      @if (provider.logo_url) {
                        <img [src]="provider.logo_url" [alt]="provider.display_name" class="size-5 object-contain" />
                      } @else {
                        <svg class="size-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                        </svg>
                      }
                      <span>Sign in with {{ provider.display_name }}</span>
                    }
                  </button>
                }

                @if (providersLoading()) {
                  <div class="flex justify-center py-2">
                    <div class="size-5 border-2 border-gray-300 dark:border-gray-600 border-t-blue-600 dark:border-t-blue-400 rounded-full animate-spin" role="status">
                      <span class="sr-only">Loading providers</span>
                    </div>
                  </div>
                }
              </div>
            }

            <!-- Neither mode is configured -->
            @if (!showLocalForm() && !showSsoSection() && !providersLoading()) {
              <p class="text-sm text-gray-500 dark:text-gray-400 text-center">
                No authentication method is configured. Set <code class="font-mono">LOCAL_AUTH_ENABLED=true</code> or configure an OIDC provider.
              </p>
            }
          </div>
        </div>
      </div>
    </div>
  `
})
export class LoginPage implements OnInit, OnDestroy {
  protected readonly authService = inject(AuthService);
  private readonly userService = inject(UserService);
  private readonly sidenavService = inject(SidenavService);
  private readonly config = inject(ConfigService);
  private readonly http = inject(HttpClient);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly systemService = inject(SystemService);
  private readonly sessionService = inject(SessionService);
  private readonly fb = inject(FormBuilder);

  readonly localForm = this.fb.group({
    email: ['', [Validators.required, Validators.email]],
    password: ['', [Validators.required]],
  });

  readonly isLoading = signal(false);
  readonly errorMessage = signal<string | null>(null);
  readonly providers = signal<AuthProviderPublicInfo[]>([]);
  readonly providersLoading = signal(true);
  readonly activeProviderId = signal<string | null>(null);
  readonly localLoginActive = signal(false);

  readonly showLocalForm = computed(() => this.authService.isLocalAuthEnabled());
  readonly showSsoSection = computed(
    () => this.authService.isOidcConfigured() || this.providers().length > 0
  );

  ngOnInit(): void {
    this.sidenavService.hide();
    this.checkFirstBootStatus();
    this.loadProviders();
  }

  ngOnDestroy(): void {
    this.sidenavService.show();
  }

  private async checkFirstBootStatus(): Promise<void> {
    try {
      const completed = await this.systemService.checkStatus();
      if (!completed) {
        this.router.navigate(['/auth/first-boot']);
      }
    } catch {
      // stay on login page if status check fails
    }
  }

  private async loadProviders(): Promise<void> {
    try {
      const url = `${this.config.appApiUrl()}/auth/providers`;
      const response = await firstValueFrom(
        this.http.get<AuthProviderPublicListResponse>(url)
      );
      this.providers.set(response?.providers ?? []);
    } catch {
      this.providers.set([]);
    } finally {
      this.providersLoading.set(false);
    }
  }

  async handleLocalLogin(): Promise<void> {
    if (this.localForm.invalid || this.isLoading()) return;

    this.isLoading.set(true);
    this.localLoginActive.set(true);
    this.errorMessage.set(null);

    const { email, password } = this.localForm.value;

    try {
      await this.authService.localLogin(email!, password!);
      this.userService.refreshUser();
      await this.userService.ensurePermissionsLoaded();
      this.sessionService.enableSessionsLoading();
      this.navigateAfterLogin();
    } catch (error) {
      this.errorMessage.set(error instanceof Error ? error.message : 'Sign in failed. Please try again.');
    } finally {
      this.isLoading.set(false);
      this.localLoginActive.set(false);
    }
  }

  async handleSsoLogin(): Promise<void> {
    this.isLoading.set(true);
    this.localLoginActive.set(false);
    this.activeProviderId.set(null);
    this.errorMessage.set(null);

    try {
      this.storeReturnUrl();
      await this.authService.login();
    } catch (error) {
      this.isLoading.set(false);
      this.errorMessage.set(error instanceof Error ? error.message : 'An error occurred during sign in');
    }
  }

  async handleProviderLogin(provider: AuthProviderPublicInfo): Promise<void> {
    this.isLoading.set(true);
    this.localLoginActive.set(false);
    this.activeProviderId.set(provider.provider_id);
    this.errorMessage.set(null);

    try {
      this.storeReturnUrl();
      await this.authService.login(provider.provider_id);
    } catch (error) {
      this.isLoading.set(false);
      this.activeProviderId.set(null);
      this.errorMessage.set(error instanceof Error ? error.message : 'An error occurred during sign in');
    }
  }

  private navigateAfterLogin(): void {
    const returnUrl = sessionStorage.getItem('auth_return_url');
    sessionStorage.removeItem('auth_return_url');
    this.router.navigateByUrl(returnUrl && returnUrl !== '/auth/login' ? returnUrl : '/');
  }

  private storeReturnUrl(): void {
    const returnUrl = this.route.snapshot.queryParams['returnUrl'];

    let finalDestination: string | undefined;
    if (returnUrl) {
      finalDestination = returnUrl.startsWith('/') ? returnUrl : `/${returnUrl}`;
    } else {
      const referrer = document.referrer;
      if (referrer) {
        try {
          const referrerUrl = new URL(referrer);
          if (referrerUrl.origin === window.location.origin) {
            const referrerPath = referrerUrl.pathname + referrerUrl.search;
            if (referrerPath !== '/auth/login' && referrerPath !== '/auth/callback') {
              finalDestination = referrerPath;
            }
          }
        } catch {
          // invalid referrer, ignore
        }
      }
    }

    if (finalDestination) {
      sessionStorage.setItem('auth_return_url', finalDestination);
    }
  }
}
