# Pendientes antes de subir las guías al bucket

**Este archivo NO se sube al bucket.** Son las preguntas que el código de la PWA no
contesta. Cada una dice qué guía y qué sección toca. Las guías están escritas solo
con lo que el código confirma; lo marcado con ⚠️ se escribió con un supuesto
razonable y hay que validarlo o quitarlo antes de subir.

Fuente de los hechos: lectura del repo `pwa-dxkre` (rama `develop`, 2026-10-19),
sin ejecutar la app. Lo que cambia por sitio vive en Strapi y en core-api.

## ⚠️ Supuestos escritos en las guías (validar o quitar)

| Guía | Sección | Supuesto | Qué hay que confirmar |
|---|---|---|---|
| cuenta_y_acceso | 2. Póliza | "pídeselo a tu empresa o aseguradora" | ¿De dónde saca el código el usuario de cada empresa (RH, credencial, correo de bienvenida)? Si cambia por sitio, conviene una tabla por empresa. |
| cuenta_y_acceso | 6. Instalar (Android) | Menú ⋮ de Chrome → "Instalar app" / "Agregar a la pantalla principal" | Es el comportamiento estándar de Chrome, no de la PWA. Probarlo en un Android real. |
| receta_estudios_y_servicios | 3. Servicios | "no ofrece odontología ni ambulancia" | No existe en ningún slug ni texto de la PWA; confirmar que ningún sitio lo tiene contratado. |
| receta_estudios_y_servicios | 4. Emergencias | "en México, el 911" | La app elige el número por zona horaria (MX = 911). Confirmar para rappi-pt (Brasil, 192). |

## Preguntas abiertas (no están en las guías)

**Asesorías**
- ¿Qué servicios tiene activos cada sitio (saludgs, mcdonalds, equipo, rappi-pt) y cuáles cobran?
- ¿Cuántos días hacia adelante abre Healthie la agenda y a qué hora se liberan horarios? (en el código hay un comentario de "15 días, horarios nuevos cada día", sin confirmar)
- ¿Cuántos días de intervalo tienen Psicología y Nutrición entre asesorías?
- ¿"No te presentaste" bloquea volver a agendar o cuenta para el intervalo? ¿El profesional reintenta la llamada?
- ¿Desde qué número llama el profesional en las asesorías por llamada?
- ¿Qué correo o WhatsApp recibe el usuario al agendar y antes de la cita?
- ¿Qué hace el usuario si necesita cancelar o reagendar faltando menos de 1 hora?
- ¿Se atiende a menores en Psicología, Nutrición o Medicina General? ¿Edad mínima en Pediatría?

**Receta**
- ¿Cuánto tarda en aparecer la receta en "Material compartido" después de la asesoría?
- ¿La receta también llega por correo o WhatsApp (Prescrypto)?
- En sitios cuyo inicio es `/dashboard`, ¿el menú de Strapi incluye "Mis asesorías"? Si no, la guía manda a una opción que el usuario no ve.

**Cuenta**
- ¿Cuántos intentos fallidos bloquean la cuenta y por cuánto tiempo?
- ¿Qué sitios usan el acceso de colaborador (número de empleado)?

**Membresía**
- ¿En qué sitios está activo "Invitar a un familiar" y con cuántos lugares?

## Bugs de la PWA encontrados (reportar al equipo de la PWA)

1. **Registro con éxito falso:** si falla el `POST` de alta, el wizard muestra "¡Listo! Ya tienes tu cuenta." sin crearla (`components/register/steps/PasswordStep.tsx:280-288`). Explica tickets de "correo no registrado" (#35).
2. **Mensaje equivocado en acceso de colaborador:** puede mostrar "Credenciales incorrectas… usuario y contraseña" en un formulario sin contraseña (`lib/utils/errorMessages.ts:35-36`). Explica el ticket #20.
3. **Descarga de receta:** el ícono de descarga solo abre la vista previa; si la vista previa falla, el mensaje pide descargar y no hay cómo (`components/followUp/MaterialsList.tsx:39-41`).
4. **Cupo de familiares fijo:** "Ya usaste los 4 lugares." está escrito con 4 aunque el máximo real es `number_of_guests` (`es.json:3015`).
5. **Banner de instalación:** solo aparece en `/dashboard`; en sitios con inicio CORA Vitals nunca sale (`components/app/PWAInstallBanner.tsx:109-112`).
