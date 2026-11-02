# Этап сборки
FROM mcr.microsoft.com/dotnet/sdk:9.0 AS build
WORKDIR /src

# Копируем csproj и восстанавливаем зависимости
COPY ["bottg.csproj", "./"]
RUN dotnet restore "bottg.csproj"

# Копируем весь проект и публикуем
COPY . .
RUN dotnet publish "bottg.csproj" -c Release -o /app/publish /p:UseAppHost=false

# Финальный рантайм
FROM mcr.microsoft.com/dotnet/aspnet:9.0 AS final
WORKDIR /app

# Обязательные настройки для Render
ENV ASPNETCORE_URLS=http://+:8080
EXPOSE 8080

# Копируем опубликованное приложение
COPY --from=build /app/publish .

# Точка входа
ENTRYPOINT ["dotnet", "bottg.dll"]
