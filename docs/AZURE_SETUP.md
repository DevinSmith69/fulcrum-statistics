# AZURE_SETUP — tonight's checklist (≈ 45 min)

Everything goes in **one region**. Use **Sweden Central**, which has good Foundry model availability (fallback: East US 2).
Use one resource group, `rg-fulcrum`. Naming suffix: your initials plus 3 digits (e.g. `ds042`) to keep names unique.

## 1. Subscription (portal)
1. Go to portal.azure.com, sign up with your personal Microsoft account, and complete the card verification.
2. **Upgrade to Pay-As-You-Go immediately** (Subscriptions → your sub → Upgrade). You keep the free credit,
   and free-trial subscriptions often get little or no quota for Foundry models.
3. Cost Management → Budgets → create **£50 monthly** budget with alerts at 50/80/100 %.
4. Install CLI tools locally: `az` (Azure CLI), `azd` (Azure Developer CLI), Docker Desktop. Then:
   ```bash
   az login && azd auth login
   az account show --query "{sub:id, name:name}" -o table
   az group create -n rg-fulcrum -l swedencentral
   ```
5. Register providers (avoids surprises on Sunday):
   ```bash
   for p in Microsoft.App Microsoft.EventHub Microsoft.CognitiveServices Microsoft.OperationalInsights \
            Microsoft.Insights Microsoft.ContainerRegistry Microsoft.KeyVault Microsoft.Web; do
     az provider register -n $p; done
   ```

## 2. Microsoft Foundry project (portal: ai.azure.com)
1. Create a new **Foundry project** in `rg-fulcrum` in Sweden Central (this creates the Foundry resource as well).
2. Models → deploy:
   - a **fast mini chat model** (the current GPT-4.1-mini / 4o-mini class, whichever the catalog offers),
     deployment name `fast`, Global Standard, and raise the TPM to whatever quota allows (≥ 50k TPM is plenty);
   - optionally a **stronger model** for recaps, deployment name `strong`.
3. Copy the **project endpoint** (Overview page, `https://<resource>.services.ai.azure.com/api/projects/<project>`).
4. Test it in the playground: ask it for JSON. If deployment fails with a quota error, try East US 2 or a different
   model SKU. Don't burn time here.
5. Give yourself the **Azure AI User** role on the project (IAM) so `DefaultAzureCredential` works locally.

## 3. Event Hubs (CLI) — Standard tier is required for the Kafka endpoint
```bash
NS=evh-fulcrum-<suffix>
az eventhubs namespace create -g rg-fulcrum -n $NS -l swedencentral --sku Standard
for h in match-events tracking-frames match-state moments insights; do
  az eventhubs eventhub create -g rg-fulcrum --namespace-name $NS -n $h --partition-count 2
done
for cg in engine api-feed; do az eventhubs eventhub consumer-group create -g rg-fulcrum --namespace-name $NS --eventhub-name match-events -n $cg; done
az eventhubs eventhub consumer-group create -g rg-fulcrum --namespace-name $NS --eventhub-name tracking-frames -n engine
az eventhubs eventhub consumer-group create -g rg-fulcrum --namespace-name $NS --eventhub-name match-state -n api
for cg in agents api; do az eventhubs eventhub consumer-group create -g rg-fulcrum --namespace-name $NS --eventhub-name moments -n $cg; done
az eventhubs eventhub consumer-group create -g rg-fulcrum --namespace-name $NS --eventhub-name insights -n api
az eventhubs namespace authorization-rule keys list -g rg-fulcrum --namespace-name $NS \
  -n RootManageSharedAccessKey --query primaryConnectionString -o tsv   # → .env (dev only)
```
(WS5's Bicep will codify all of this on Saturday. Doing it by hand tonight just lets you test the bus early.)

## 4. Local `.env`
```
FULCRUM_BUS=memory
FULCRUM_LLM=fake
EVENTHUBS_NAMESPACE=evh-fulcrum-<suffix>
EVENTHUBS_CONNECTION_STRING=<from step 3>
FOUNDRY_PROJECT_ENDPOINT=<from step 2>
FOUNDRY_MODEL_DEPLOYMENT=fast
FOUNDRY_MODEL_DEPLOYMENT_RECAP=strong
```

## 5. GitHub
Create `fulcrum` (private for now, **make it public Monday** — required for judging). Push `main` with this
starter kit, then create the worktrees from BUILD_PLAN.md.

## Expected spend for the weekend
Event Hubs Standard (1 TU) ≈ £0.60/day · Container Apps (consumption, scale-to-zero) ≈ pennies ·
Foundry mini model ≈ < £1 per full match · Static Web App free tier · Log Analytics small. **Total < £20.**
Tear down after judging: `azd down --purge`, or delete `rg-fulcrum`.
